"""Accessibility-tree capture and diffing — the judge's primary state evidence.

Why AX instead of source code or an innerText snippet:

  * The checks are behavioral ("pause halts progression", "the toggle changes
    the beat bar"), and a before/after AX diff is DIRECT evidence that state
    changed. Source code only supports an inference about what should happen.
  * The old evidence was `innerText[:200]`, which cannot show a modal opening,
    an `aria-expanded` flip, or a slider's `valuenow` moving. The judge kept
    writing "the DOM remained unchanged" for interactions that plainly worked:
    one b0811 task was gated to 0.0 on "Launch Assistant opens chat widget"
    while the AX diff shows +StaticText "Assistant launched", +button
    expanded=True, +textbox "Message" focused=True. 137 tokens of decisive
    evidence against 10,000 tokens of source that did not contain the answer.
  * It is also smaller: measured over 8 artifacts, mean 9.9k chars of AX vs a
    40k code slice — and unlike the slice, the AX tree is COMPLETE.

AX does not see inside a canvas, so WebGL/particle artifacts still need the
before/after frame pair. This module is additive to that, never a replacement.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

# Roles carrying no state a checklist item can turn on. InlineTextBox in
# particular duplicates every StaticText and inflates the tree by ~40%.
_NOISE_ROLES = frozenset({"InlineTextBox", "LineBreak", "none", "presentation"})

# Properties worth reporting: each one is a control's observable state, which is
# exactly what a directional check asserts about.
_KEEP_PROPS = ("checked", "selected", "expanded", "pressed", "disabled",
               "valuenow", "valuetext", "focused", "level", "invalid")

MAX_NAME = 80
# Cap on nodes serialized into one snapshot. A dashboard with a 5000-row table
# would otherwise swamp the prompt with repeated cells; the diff is what carries
# the signal, and it stays complete because it is computed BEFORE truncation.
MAX_NODES = 400

# A capture slower than this marks the page as AX-hostile and disables further
# captures for the rest of that trajectory (see `capture`). Playwright's sync
# CDPSession.send() accepts NO timeout — verified against the installed API — and
# the sync API is thread-affine, so a single call cannot be hard-bounded from
# here IN-THREAD. What CAN be bounded is accumulation, which is the observed cost:
# observe() runs up to 3x per step over up to 40 steps, so a 20s capture (measured
# on one real artifact) would be 40min of pure AX on a page that is telling us
# plainly that its renderer is saturated. One slow call is the most any trajectory
# pays.
SLOW_CAPTURE_S = 5.0

# DEFECT 120. SLOW_CAPTURE_S bounds ACCUMULATION but not a single call that never
# returns. `getFullAXTree` (and `new_cdp_session`) go over the CDP pipe, and that
# pipe can wedge *hard* — not slow-but-returning, which the strike handles, but a
# blocking recv that never completes (a renderer whose CDP transport hangs while
# the JS DOM read the observe() canary tries first still answered fine, so
# `_dom_too_slow` never latched and this call still ran). Reproduced on b0812 s20
# `websites_showcase_personal-websi__YeQDbtW`: execute reached step 9, captured a
# `_before` frame, then the next observe()'s `getFullAXTree` wedged the worker in
# `do_wait` for the whole 30-min driver stall window — THREE times (original run +
# two requeues), each time killing the shard's whole pass and leaving 0 verdicts.
# The DOM-read canary is a CORRELATED predictor of a saturated renderer, not of a
# CDP-transport wedge, so it does not cover this. The fix is the same daemon-thread
# wall-clock bound this codebase already uses for the other thread-affine calls it
# cannot cut in-thread (pipeline._score_with_timeout, run._load_with_timeout): run
# the CDP send on a daemon thread and abandon it after AX_CDP_TIMEOUT_S. The
# abandoned thread keeps holding the dead pipe but cannot block interpreter exit;
# capture() returns [] and latches `_ax_too_slow` so no later observe pays it
# again. Sized well above SLOW_CAPTURE_S (a genuinely slow-but-live 20s call still
# completes) and well under the 15s per-observe / 600s exec budgets it protects.
AX_CDP_TIMEOUT_S = float(os.environ.get("IV_AX_CDP_TIMEOUT_S", "30"))


def capture(page: Any) -> list[tuple[str, str, tuple]]:
    """The page's accessibility tree as (role, name, props) triples.

    Returns [] on any failure — a missing AX tree costs one kind of evidence and
    must never abort the step. Uses CDP directly rather than Playwright's
    `page.accessibility.snapshot()`: the latter returns a nested tree that is
    both larger to serialize and harder to diff, and it drops the ARIA state
    properties that make a diff decisive.

    A page whose first capture exceeds SLOW_CAPTURE_S is flagged on the page
    object and skipped from then on: the call cannot be bounded individually, so
    the bound is on how many times we are willing to pay it.
    """
    if getattr(page, "_ax_too_slow", False):
        return []
    t0 = time.monotonic()
    # DEFECT 120: run the two thread-affine CDP calls (session open + getFullAXTree)
    # on a daemon thread and abandon them after AX_CDP_TIMEOUT_S. A wedged CDP pipe
    # cannot be cut in-thread (CDPSession.send takes no timeout), so this out-of-
    # thread bound is the only net for a call that never returns. On timeout we
    # latch `_ax_too_slow` and return [] — one lost AX snapshot, not a hung shard.
    box: dict[str, Any] = {}

    def _work() -> None:
        try:
            session = page.context.new_cdp_session(page)
        except Exception:  # noqa: BLE001 - fake pages / non-Chromium / no CDP
            box["res"] = None
            return
        try:
            box["res"] = session.send("Accessibility.getFullAXTree")
        except Exception:  # noqa: BLE001
            box["res"] = None
        finally:
            try:
                session.detach()
            except Exception:
                pass

    th = threading.Thread(target=_work, name="ax-cdp", daemon=True)
    th.start()
    th.join(AX_CDP_TIMEOUT_S)
    elapsed = time.monotonic() - t0
    if th.is_alive():
        # The send is wedged. Abandon the thread (it holds the dead pipe but cannot
        # block exit) and disable AX for the rest of this trajectory.
        try:
            page._ax_too_slow = True
        except Exception:  # noqa: BLE001 - can't annotate; accept the cost
            pass
        return []
    if elapsed > SLOW_CAPTURE_S:
        try:
            page._ax_too_slow = True
        except Exception:  # noqa: BLE001 - can't annotate; accept the cost
            pass
    res = box.get("res")
    if not res:  # None (session/send failed) or empty
        return []

    out: list[tuple[str, str, tuple]] = []
    for n in (res.get("nodes") or []):
        if n.get("ignored"):
            continue
        role = ((n.get("role") or {}).get("value") or "")
        if not role or role in _NOISE_ROLES:
            continue
        name = str((n.get("name") or {}).get("value") or "").strip()
        props = []
        for p in (n.get("properties") or []):
            pname = p.get("name")
            if pname in _KEEP_PROPS:
                val = (p.get("value") or {}).get("value")
                # A false/absent state is the default for every control; listing
                # it would make two identical pages diff on nothing.
                if val not in (False, "false", None, ""):
                    props.append((pname, val))
        out.append((role, name[:MAX_NAME], tuple(sorted(props))))
    return out


def _fmt(node: tuple[str, str, tuple]) -> str:
    role, name, props = node
    tail = "".join(f" {k}={v}" for k, v in props)
    return f'{role} "{name}"{tail}' if name else f"{role}{tail}"


def to_text(nodes: list[tuple[str, str, tuple]], limit: int = MAX_NODES) -> str:
    if not nodes:
        return ""
    body = "\n".join(_fmt(n) for n in nodes[:limit])
    if len(nodes) > limit:
        body += f"\n... [{len(nodes) - limit} more nodes]"
    return body


def diff(before: list[tuple[str, str, tuple]],
         after: list[tuple[str, str, tuple]], limit: int = 40) -> str:
    """What the interaction changed, as +/- lines.

    Multiset-aware: a page that gains a second identical row must report one
    addition, not zero. A plain set difference reported "no change" for exactly
    the repeated-element updates (list items, grid cells, beat dots) that many
    checks are about.
    """
    if not before and not after:
        return ""
    from collections import Counter
    cb, ca = Counter(before), Counter(after)
    added = list((ca - cb).elements())
    removed = list((cb - ca).elements())
    if not added and not removed:
        # An explicit statement, not an empty string: "nothing changed" is
        # itself determinate evidence for a check asserting something should
        # have, and the judge must be able to tell it apart from "not captured".
        return "(no accessibility-tree change)"
    lines = [f"+ {_fmt(n)}" for n in added[:limit]]
    lines += [f"- {_fmt(n)}" for n in removed[:limit]]
    extra = max(0, len(added) - limit) + max(0, len(removed) - limit)
    if extra:
        lines.append(f"... [{extra} more changes]")
    return "\n".join(lines)
