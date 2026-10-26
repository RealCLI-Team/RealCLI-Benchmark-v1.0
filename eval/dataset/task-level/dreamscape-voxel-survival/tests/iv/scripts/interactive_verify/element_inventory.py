"""What controls actually exist, per page — the planner's tier-2 observation.

The planner is asked to write an action sequence for every checklist item, and
until now it was shown one screenshot plus 6000 characters of the ENTRY page's
`innerText` (`executor.observe`). That is enough to guess that a control exists.
It is not enough to know *what kind* of control it is, and it says nothing at all
about the artifact's other pages.

The gap was an asymmetry inside this package rather than a limit of the approach:
`harbor_loader.capture_axtree` already renders the page and hands a pruned
accessibility tree to CHECKLIST generation, which prefers it to the raw HTML. So
the checklist author sees `slider "Wind speed" valuenow=40` while the planner
writing the drag for that very item saw a truncated text dump. Three measured
consequences, all of which look like the artifact's fault in the verdict:

  * Wrong verb for the widget. A `click` on a slider, or the click-the-combobox
    then click-the-option pair that `collapse_select_pairs` exists to repair, are
    both "the model could not tell what it was aiming at".
  * Targets that exist but are unreachable from where the plan stands. On the
    64-task paired arm, 53.3% of `selector not found` outcomes named a target
    that IS present in the workspace — behind a tab never selected or a wizard
    never advanced. Those are not inventions, so defect 103's grounding check
    cannot see them, which is why regenerating plans moved determinacy by less
    than the noise floor.
  * Multi-page artifacts planned entirely against the landing page. `goto` and
    `same_origin_links` (defect 97) gave the planner the page *addresses*; this
    gives it the controls on the far side of them.

What this is NOT: knowing what an element DOES when actuated. A button's role
and name do not reveal that it opens a modal which must be dismissed before the
next step can land. That is tier 3, it is only knowable by pressing the thing,
and a one-shot planner cannot have it. This module closes tier 2 only.

Two invariants, both already paid for elsewhere in this package:

**Fail open.** Every failure path returns an inventory that is merely smaller,
never an exception and never a wrong claim. An empty inventory reproduces exactly
today's behaviour (the prompt block is omitted), so a capture that breaks costs
the planner some context and costs the task nothing.

**Never touch the scored page.** The walk runs in its own browser context, and
the entry page is read from the AX tree `observe()` already captured. Two
concrete hazards this avoids: `axtree.capture` latches `page._ax_too_slow` after
one slow call and would then withhold the AX diff — the judge's primary state
evidence — for the whole trajectory; and navigating the scored context would
share `localStorage`, so probing a game that records "intro seen" would make the
scored run skip an intro the plan's `advance` steps still expect.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from scripts.interactive_verify import axtree

# Roles a plan can actuate. Matched case-insensitively: Chrome's AX role names
# mix ARIA spellings ("button", "combobox") with its own internal ones
# ("DisclosureTriangle", "ColorWell"), and a set that silently missed half of
# them would report a rich page as having no controls.
ACTUABLE_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox",
    "listbox", "option", "slider", "sliderthumb", "spinbutton", "switch", "tab",
    "menuitem", "menuitemcheckbox", "menuitemradio", "treeitem", "togglebutton",
    "popupbutton", "disclosuretriangle", "colorwell", "menubutton", "menulistpopup",
    "menulistoption", "datetime", "inputtime", "combo",
})
# Not actuable, but they decide the SHAPE of the plan. A page whose only
# interactive node is a canvas is a keyboard/pointer artifact, and telling the
# planner "there are no buttons, there is a canvas" is what stops it emitting
# clicks on a remembered HUD. `dialog` matters for the same reason in reverse:
# something is already open at load time.
CONTEXT_ROLES = frozenset({"canvas", "video", "audio", "dialog", "alertdialog", "iframe"})

MAX_PAGES = 6          # linked pages walked, on top of the entry page
MAX_PER_PAGE = 120     # distinct control lines per page
BUDGET_S = 20.0        # wall-clock ceiling for the whole walk
SETTLE_MS = 900
VIEWPORT = {"width": 1280, "height": 720}


def _keep(role: str) -> bool:
    r = (role or "").lower()
    return r in ACTUABLE_ROLES or r in CONTEXT_ROLES


def summarize(nodes: list[tuple[str, str, tuple]],
              limit: int = MAX_PER_PAGE) -> tuple[list[str], int]:
    """Actuable nodes as `role "name" prop=val` lines, deduplicated with counts.

    Returns (lines, total_kept). Deduplication is not cosmetic: a data grid can
    carry hundreds of identical `button "Edit"` nodes, and listing each one would
    push the page's real controls past `limit` while telling the planner nothing
    it does not learn from `button "Edit" x240`.
    """
    seen: dict[tuple[str, str, tuple], int] = {}
    for n in nodes or ():
        try:
            role, name, props = n[0], n[1], tuple(n[2] or ())
        except (TypeError, IndexError):
            continue
        if not _keep(role):
            continue
        # An unnamed node is only worth reporting for the context roles, where
        # presence IS the signal (`canvas ""`). An unnamed button is unaimable
        # and listing it would invite the planner to aim at it.
        if not str(name).strip() and (role or "").lower() not in CONTEXT_ROLES:
            continue
        key = (str(role), str(name).strip(), props)
        seen[key] = seen.get(key, 0) + 1
    lines = []
    for (role, name, props), n in list(seen.items())[:limit]:
        tail = "".join(f" {k}={v}" for k, v in props)
        head = f'{role} "{name}"' if name else role
        lines.append(f"{head}{tail}" + (f" x{n}" if n > 1 else ""))
    return lines, len(seen)


def _page_entry(url: str, label: str, nodes: list) -> dict[str, Any]:
    lines, total = summarize(nodes)
    return {"url": url, "label": label, "elements": lines,
            "total": total, "truncated": total > len(lines),
            "ax_nodes": len(nodes or ())}


def build(page: Any, entry_url: str, links: Any, entry_ax: Any = None, *,
          max_pages: int = MAX_PAGES, budget_s: float = BUDGET_S,
          navigate: Callable | None = None,
          settle: Callable | None = None,
          resolve: Callable | None = None) -> dict[str, Any]:
    """Inventory the entry page and up to ``max_pages`` of its own pages.

    ``entry_ax`` is the tree ``observe()`` already captured for the planner's
    screenshot — reused rather than recaptured, so the entry page costs nothing
    and describes exactly the state the run will start from.

    Never raises. ``error`` is set and the walk is cut short on any failure; the
    entry page survives a failure of the multi-page walk, because the two are
    independent and the cheap half carries most of the value.
    """
    t0 = time.monotonic()
    out: dict[str, Any] = {
        "pages": [], "pages_walked": 0, "pages_skipped": 0,
        "truncated": False, "error": None, "budget_s": budget_s, "wall_s": 0.0,
    }

    def _finish() -> dict[str, Any]:
        out["wall_s"] = round(time.monotonic() - t0, 2)
        return out

    # --- the entry page: free, and cannot fail in a way that matters ----------
    try:
        out["pages"].append(_page_entry(entry_url, "entry page", list(entry_ax or [])))
    except Exception as exc:  # noqa: BLE001 - an inventory must never fail a task
        out["error"] = f"entry: {type(exc).__name__}: {exc}"[:200]
        return _finish()

    if not (isinstance(links, list) and links) or max_pages <= 0:
        return _finish()

    if navigate is None or settle is None or resolve is None:
        # Imported lazily: `executor` pulls Playwright and the whole browser
        # stack, and this module is imported by the planner path in contexts
        # (smoke tests, replay tooling) that have neither.
        try:
            from scripts.interactive_verify import executor as _ex
            navigate = navigate or _ex.navigate
            settle = settle or (lambda pg: _ex.settle(pg, SETTLE_MS))
            resolve = resolve or _ex.resolve_same_origin
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"executor unavailable: {type(exc).__name__}"[:200]
            return _finish()

    # `same_origin_links` returns the RAW href, because the planner's `goto` is
    # given the same string and the executor resolves it per-step. A relative
    # href ("flow/") navigated on a fresh probe page resolves against about:blank
    # and loads nothing — measured on the 4-page portfolio `zxtyfcx`, where all 6
    # linked pages came back with a 1-node AX tree (RootWebArea alone) and the
    # inventory silently described a multi-page artifact as single-page. So
    # resolve here through the SAME resolver the verb uses, and keep the raw href
    # for display so the model can cross-reference the OTHER PAGES block.
    dests = []
    for l in (links if isinstance(links, list) else ()):
        if not (isinstance(l, dict) and l.get("url")):
            continue
        href = str(l["url"])
        try:
            # None is a REFUSAL, not "couldn't tell": the resolver returns it for
            # anything off-origin, and falling back to the raw href there would
            # send the probe to somebody else's site and inventory its controls as
            # if they were the artifact's. `same_origin_links` already filters,
            # so this is depth rather than the only guard — but it is the guard
            # that holds when a caller passes an unfiltered list.
            dest = resolve(entry_url, href)
        except Exception:  # noqa: BLE001 - a bad href is one page, not the walk
            dest = None
        if not dest:
            out["pages_skipped"] += 1
            continue
        dests.append((href, dest, str(l.get("text") or "")))
    if not dests or max_pages <= 0:
        return _finish()
    if len(dests) > max_pages:
        out["pages_skipped"] = len(dests) - max_pages
        out["truncated"] = True
        dests = dests[:max_pages]

    # --- the linked pages: own context, so nothing here can reach the run ----
    ctx = probe = None
    try:
        browser = page.context.browser
        ctx = browser.new_context(viewport=dict(VIEWPORT))
        probe = ctx.new_page()
    except Exception as exc:  # noqa: BLE001
        # No isolated context => do not walk at all. Falling back to the scored
        # page would trade a missing half-feature for a contaminated run.
        out["error"] = f"probe context: {type(exc).__name__}: {exc}"[:200]
        out["truncated"] = bool(dests)
        out["pages_skipped"] = len(dests)
        try:
            if ctx is not None:
                ctx.close()
        except Exception:
            pass
        return _finish()

    try:
        for href, dest, text in dests:
            if time.monotonic() - t0 > budget_s:
                out["truncated"] = True
                out["pages_skipped"] += 1
                continue
            try:
                navigate(probe, dest)
                settle(probe)
                nodes = axtree.capture(probe)
            except Exception as exc:  # noqa: BLE001 - one bad page, not the walk
                out["pages"].append({"url": href, "label": text or "linked page",
                                     "elements": [], "total": 0, "truncated": False,
                                     "ax_nodes": 0,
                                     "error": f"{type(exc).__name__}: {exc}"[:120]})
                continue
            out["pages"].append(_page_entry(href, text or "linked page", nodes))
            out["pages_walked"] += 1
    finally:
        for obj in (probe, ctx):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
    return _finish()
