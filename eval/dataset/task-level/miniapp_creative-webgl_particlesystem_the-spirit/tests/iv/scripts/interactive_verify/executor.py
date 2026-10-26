#!/usr/bin/env python3
"""Stage 2 — execute the planned actions in a real browser, record the trajectory.

Drives a Playwright page through the action list, capturing a screenshot before
the first action and after every action, plus a DOM snapshot and any new console/
page errors per step. Frames are persisted under ``out_dir/frames/`` as
``NNN_<action>.png``. The Playwright video (if the context was created with
record_video) is copied in by the caller/run layer.
"""
from __future__ import annotations

import base64
import os
import re
import posixpath
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scripts.interactive_verify import axtree
from scripts.interactive_verify._bootstrap import ensure_service_path

ensure_service_path()
from service.verify import browser_actions  # noqa: E402

SETTLE_MS = 1200  # give SPAs time to mount/hydrate before we observe or plan
# Extra dwell between a before_after probe's action and its AFTER frame. Many
# effects are not instantaneous: an emission-rate slider repopulates a particle
# field over seconds, a camera eases, a chart animates in. Capturing the AFTER
# frame in the same tick as the action photographs the BEFORE state twice, and
# the judge — correctly, given that evidence — reports that nothing happened.
# Only paid on probe steps (a handful per task), not on every action.
PROBE_DWELL_MS = int(os.environ.get("IV_PROBE_DWELL_MS", "900"))
# Ceiling on the dwell when the plan asks for longer than PROBE_DWELL_MS. The
# generator authors `{"action":"drag",...,"capture":"before_after"}` followed by
# `{"action":"wait","ms":N}` precisely so the effect can land before it is judged
# — but that wait runs AFTER the AFTER frame, where it cannot help. So the
# authored N is honoured as the dwell instead (see the lookahead in
# run_trajectory). Bounded because a plan is free to ask for 30s and 40 steps of
# that is a stalled worker.
PROBE_DWELL_MAX_MS = int(os.environ.get("IV_PROBE_DWELL_MAX_MS", "6000"))
# DEFECT 67. Two frames at the ends of the dwell are a sample of size two from a
# continuous process, and several of the questions the checklist asks are about
# the process, not about its endpoints:
#
#   "the MPM lava particles are visible FLOWING across the terrain"
#   "the camera ORBITS the scene"          (user: "needs a longer waiting time")
#   "clicking Pause STOPS the animation"   (user: "the checking approach for
#                                            Pause is stupid as the screenshots
#                                            before and after pausing are indeed
#                                            different")
#
# The Pause case is the clean proof that the pair is the wrong instrument, not
# merely a short one: a paused animation is still one frame further along than it
# was when the click landed, so BEFORE != AFTER holds for a Pause that WORKS and
# for a Pause that does NOTHING. No dwell length fixes that. What distinguishes
# them is whether consecutive frames stop differing from EACH OTHER, which needs
# at least three samples.
#
# So sample ACROSS the dwell instead of only at its end. The dwell is already
# being paid — we sleep it and then take one frame — so the extra frames cost
# only the screenshots themselves (~50ms each, off the compositor, no renderer
# main thread), and the sleeps are shortened to keep the AFTER frame at the same
# wall-clock instant it landed on before. Total added time per probe step is
# BURST_FRAMES * ~50ms; the evidence added is a measured motion timeline.
BURST_FRAMES = int(os.environ.get("IV_BURST_FRAMES", "3"))
# Don't sample faster than this: two frames 40ms apart differ by one animation
# tick on everything, which would make "still moving" the answer for every
# artifact and destroy the pause signal. Also caps the count on a short dwell.
BURST_MIN_GAP_MS = int(os.environ.get("IV_BURST_MIN_GAP_MS", "200"))
# A burst frame must be cheap or it is not worth having. `observe()` may spend up
# to OBSERVE_TIMEOUT_MS because it also reads the DOM and the AX tree; a burst
# frame is a screenshot alone, and one that cannot be taken in 2s means the page
# is saturated — at which point the timeline it would produce is a measurement of
# our own scheduler, not of the artifact. Missing the deadline abandons the rest
# of the burst (the dwell is still slept in full, so pre/post are unaffected).
BURST_SHOT_TIMEOUT_MS = int(os.environ.get("IV_BURST_SHOT_TIMEOUT_MS", "2000"))
# Ceiling for a single observation capture (screenshot). Playwright's default is
# 30s, which observe() would inherit on every call — see the note in observe().
OBSERVE_TIMEOUT_MS = 8000

# Ceiling for a single bounded selector scan (`_bounded_query_all`). `page.
# query_selector_all` takes no timeout and dead-waits on a renderer pegged by
# continuous rAF/WebGL — the b0811 resume wedged here forever. Matches
# browser_actions.ADVANCE_EVAL_TIMEOUT_MS (3000): both route the same
# in-greenlet `locator(':root').evaluate*` idiom whose timeout IS honoured
# against a pegged main thread (measured: raises at 3.00s vs. never returning).
QSA_TIMEOUT_MS = int(os.environ.get("IV_QSA_TIMEOUT_MS", "3000"))

# Wall-clock ceiling on the whole action loop. Every INDIVIDUAL operation here is
# already bounded (nav 20s, action 2.5s, observe 8s, settle 3.6s), but 40 steps
# of a page that pegs the renderer main thread multiply out: on b0810 one task
# spent 6116s in exec and another 3955s, each with 40 individually-capped steps,
# and two workers sat on them for ~50 minutes while 17,833 instances waited.
# Worst case per step is roughly nav+action+3*observe ~= 30s, so 40 steps could
# legitimately want 20min; 600s stops the pathological cases and still lets a
# normal task (median exec well under 120s) finish every planned action. Hitting
# the budget truncates the trajectory and is recorded as `exec_truncated` — the
# steps that DID run keep their evidence, and items whose actions never ran have
# no pair, so they degrade to indeterminate rather than gating.
#
# Overridable because 600s is a PRODUCTION ceiling — it bounds a 17,833-instance
# sweep — and a gate round has the opposite priority: it must finish every
# planned action or its determinacy counts measure the machine. Under load the
# same six artifacts ran at 22-30s/step against 3.1-9.3s/step idle, so 4 of 6
# truncated at 600s and their scores (1.000 -> 0.000 on two of them) were not
# readable as code signals at all. Raise it for a gate round; leave it alone for
# a sweep.
EXEC_BUDGET_S = float(os.environ.get("IV_EXEC_BUDGET_S", "600"))
# Ceiling for the initial navigation. See navigate().
NAV_TIMEOUT_MS = 20000


def navigate(page: Any, url: str) -> None:
    """Open ``url``, bounded, and tolerate a slow-but-rendering page.

    A bare ``page.goto(url)`` waits for the "load" event with Playwright's 30s
    default. Heavy WebGL/3D artifacts stream assets continuously and may never
    fire "load" even though they paint fine, so the bare call turned renderable
    artifacts into hard `plan:`/`execute:` errors scored 0.0 — a false negative,
    not a real defect.

    So: wait for "domcontentloaded" (the DOM exists and scripts can run) rather
    than every subresource, and treat a timeout as non-fatal. Whether the page
    really rendered is then decided downstream by observe(), which is the
    honest test — pixels and DOM, not a navigation event.
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except TypeError:
        # Stub pages in the smoke tests take goto(url) only.
        page.goto(url)
    except Exception:
        # Slow/never-idle page: fall through and let observation decide.
        pass


def resolve_same_origin(entry_url: str, raw: str) -> str | None:
    """Absolute URL for a planned ``goto``, or None when it leaves the artifact.

    DEFECT 97. The planner is shown the artifact's file inventory but had no verb
    for "open that page", so a multi-page site could only be traversed by GUESSING
    the anchor text of a nav link. Measured on eval25_o5 `zxtyfcx` (a 4-page
    portfolio: `index.html` plus `flow/`, `paint/`, `waves/`): every inter-page hop
    was a blind `a:has-text("Fluid Particles")` / `a:has-text("Fluid Paint")`, none
    of those strings existed, and the three "return home" clicks each dead-waited
    the full 2500ms cap. 9 of 9 item-bearing steps errored and the task scored
    reward=1.0000 on the two items that survived. The page paths, by contrast, are
    known exactly -- so navigation should be addressed, not guessed.

    Same-origin is enforced here rather than trusted from the plan. A `goto` that
    escapes to the public internet would leave every later probe measuring a third
    party's page while still reporting `ok`, which is the worst failure shape we
    have: confident, wrong and invisible. For `file://` the check is containment
    under the entry's own directory, since every local file shares the null origin
    and a bare scheme/host comparison would wave through `../../etc`.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    base = urlsplit(entry_url)
    try:
        cand = urlsplit(urljoin(entry_url, raw))
    except ValueError:
        return None
    if cand.scheme != base.scheme:
        return None
    if base.scheme == "file":
        root = posixpath.dirname(base.path) or "/"
        # normpath collapses the `..` a relative href may legitimately contain;
        # containment is then a prefix test on the NORMALIZED path.
        here = posixpath.normpath(cand.path)
        if here != root and not here.startswith(root.rstrip("/") + "/"):
            return None
        return urlunsplit(("file", "", here, cand.query, cand.fragment))
    if (cand.hostname, cand.port) != (base.hostname, base.port):
        return None
    return urlunsplit(cand)


def _correct_ancestor_goto(entry_url: str, dest: str) -> bool:
    """True when a resolved same-origin goto points at a directory listing ABOVE
    the artifact -- overwhelmingly the planner's reflexive first step ``goto "/"``.

    DEFECT 126. A subpath-mounted artifact (entry ``/App/dist/index.html``) served
    by a static file server has NO app at ``/``, ``/App/`` or ``/App/dist/`` bar its
    OWN directory: those ancestor paths return the server's directory LISTING, and
    following one silently moves every later probe off the artifact, gating the
    whole trial to 0.0 as "artifact does not render". Such a destination addresses a
    DIRECTORY that is a STRICT ANCESTOR of the entry's own directory; the goto branch
    re-aims it at the entry. This is deliberately narrower than same-origin
    containment: a sibling FILE at the root (``/other.html``) or a sub-page UNDER the
    entry (``flow/``) is neither an ancestor nor a directory listing, so both keep
    navigating normally -- preserving the DEFECT 97 same-origin contract (and its
    smoke test) untouched. A root-mounted artifact (entry dir ``/``) has no strict
    ancestor, so its legitimate ``goto "/"`` is never corrected.
    """
    try:
        e = urlsplit(entry_url)
        d = urlsplit(dest)
    except ValueError:
        return False
    entry_dir = posixpath.dirname(posixpath.normpath(e.path or "/")) or "/"
    dpath = posixpath.normpath(d.path or "/")
    last = dpath.rsplit("/", 1)[-1]
    # Only a DIRECTORY listing is the failure: a bare root, a trailing-slash path,
    # or a final segment with no file extension. A concrete file (`.html`) is never
    # a directory listing and is left alone.
    if not (dpath == "/" or dpath.endswith("/") or "." not in last):
        return False
    ddir = dpath.rstrip("/") or "/"
    return entry_dir != ddir and entry_dir.startswith(ddir.rstrip("/") + "/")


_LINK_JS = """() => Array.from(document.querySelectorAll('a[href]'))
  .map(a => [a.getAttribute('href') || '', (a.textContent || '').trim().slice(0, 60)])
"""


def same_origin_links(page: Any, entry_url: str, limit: int = 40) -> list[dict[str, str]]:
    """The artifact's own pages, as (url, text) pairs, for the planner to address.

    DEFECT 97's other half. The planner reasons over a DOM snapshot truncated at
    6 KB, so on a page with a large body the nav links it needs may simply not be
    in what it was shown — and a `goto` verb it cannot aim is no better than the
    guessing it replaces. Reading the hrefs from the LIVE page instead is exact
    and costs one bounded eval.

    Same-origin filtered through the same resolver the verb itself uses, so the
    list can never advertise a destination the executor would refuse. Anchors,
    `mailto:` and `data:` URIs drop out for free (they resolve to the current
    document or to a foreign scheme). De-duplicated by destination, since a site
    typically links its own pages from several places.
    """
    try:
        raw = browser_actions._bounded_eval(page, _LINK_JS, default=None)
    except Exception:  # noqa: BLE001 - an inventory must never fail a task
        return []
    if not isinstance(raw, list):
        return []
    here = urlsplit(entry_url)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for pair in raw:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        href, text = str(pair[0]), str(pair[1])
        dest = resolve_same_origin(entry_url, href)
        if dest is None:
            continue
        # A pure fragment jump is not another page; it re-renders nothing the
        # executor could not already reach by scrolling.
        d = urlsplit(dest)
        if (d.path, d.query) == (here.path, here.query):
            continue
        if dest in seen:
            continue
        seen.add(dest)
        out.append({"url": href, "text": text})
        if len(out) >= limit:
            break
    return out


def settle(page: Any, ms: int = SETTLE_MS) -> None:
    """Best-effort wait for the page to finish mounting before observation.
    Tries network-idle then a fixed timeout; both guarded so stub pages
    (which lack these methods) are a no-op."""
    try:
        page.wait_for_load_state("networkidle", timeout=ms * 3)
    except Exception:
        pass
    try:
        page.wait_for_timeout(ms)
    except Exception:
        pass


def _cdp_screenshot(page: Any, dest: str) -> bool:
    """Capture via raw CDP, bypassing Playwright's screenshot stabilization.

    ``page.screenshot()`` waits for fonts and a stable frame before it will
    capture. Some artifacts never satisfy that gate — a continuously-animating
    canvas, or a webfont link to an unreachable CDN — so the call times out no
    matter how long we allow, and the task is scored 0.0 for lack of evidence
    even though the page paints. ``Page.captureScreenshot`` takes whatever is on
    screen right now, which for verification purposes is exactly what we want, and
    it succeeds on many pages the stabilization gate rejects (verified: a b0811
    artifact Playwright could not capture in 30s yielded a 52KB frame here).

    NOT a hard bound in-thread. DEFECT 114 measured this same call wedging on a
    fully renderer-pegged page: 25.77s on the first capture and NO return on the
    second (py-spy: parked in the CDP send at 3min+). ``CDPSession.send`` takes no
    timeout (verified) and the sync API is thread-affine, so it cannot be cut off
    IN-THREAD. The caller (`_observe_screenshot`) gates it to pages not already
    flagged renderer-hostile — but that canary (a slow DOM read) is a correlated
    predictor of a saturated renderer, not of a CDP-transport wedge, and DEFECT 120
    saw a page answer DOM reads fine yet wedge its CDP pipe. So we ALSO bound the
    call out-of-thread: run the send on a daemon thread and abandon it after
    AX_CDP_TIMEOUT_S (shared with axtree's getFullAXTree — the same thread-affine
    hazard). The abandoned thread holds the dead pipe but cannot block exit; we
    return False (no frame) rather than hang the whole trajectory.

    Returns True on success.
    """
    box: dict[str, bool] = {}

    def _work() -> None:
        try:
            ctx = page.context
            session = ctx.new_cdp_session(page)
        except Exception:  # noqa: BLE001 - fake pages / no CDP (non-Chromium)
            box["ok"] = False
            return
        try:
            res = session.send("Page.captureScreenshot", {"format": "png"})
            data = base64.b64decode(res.get("data") or "")
            if not data:
                box["ok"] = False
                return
            Path(dest).write_bytes(data)
            box["ok"] = True
        except Exception:  # noqa: BLE001
            box["ok"] = False
        finally:
            try:
                session.detach()
            except Exception:
                pass

    th = threading.Thread(target=_work, name="cdp-screenshot", daemon=True)
    th.start()
    th.join(axtree.AX_CDP_TIMEOUT_S)
    if th.is_alive():
        # Wedged CDP send — abandon the thread and mark the page renderer-hostile
        # so the rest of this trajectory skips both CDP captures (DEFECT 120).
        try:
            page._ax_too_slow = True
        except Exception:  # noqa: BLE001
            pass
        return False
    return bool(box.get("ok"))


def observe(page: Any) -> dict[str, Any]:
    """Capture {dom_snapshot, ax, screenshot(path)} defensively.

    Every call here is bounded. Playwright's default timeout is 30s and observe()
    runs up to 3x per step, so on an artifact that pegs the renderer main thread
    (continuous WebGL/rAF work) the unbounded versions cost minutes per step: one
    b0810 task burned 3571s of exec across 37 individually-capped steps. Losing a
    frame or a DOM snapshot costs one piece of evidence; hanging costs the batch.

    `set_default_timeout` alone does NOT bound the two DOM reads, and that gap
    was live. Neither `page.evaluate()` nor `page.content()` accepts a timeout
    argument, and both ignore the default because both need the renderer's main
    thread — measured against a page pegged for 30s: innerText 19.7s and
    `content()` 21.8s. Only `Locator.evaluate` honours a timeout there (2.00s vs
    22.70s on the same wedged page), so both reads route through
    `browser_actions._bounded_eval`; `content()` is replaced by a bounded
    `documentElement.outerHTML`, which is the same serialization Playwright's own
    `content()` performs. The screenshot (0.05s) does not need the main thread and
    is already bounded as written.

    Capping each call is necessary but not sufficient: three main-thread-bound
    captures (innerText, outerHTML, and the AX tree, which pays its own
    SLOW_CAPTURE_S once) still cost ~30s per observe, and observe runs up to 3x
    per step over up to 40 steps. So the cap is also a STRIKE: a page that cannot
    answer a DOM read inside the budget is telling us its renderer is saturated,
    and the next observe skips both reads for the rest of the trajectory. The
    frame is what a saturated WebGL artifact has to offer anyway, and it stays.
    This mirrors `axtree.capture`'s `_ax_too_slow` flag, for the same reason."""
    obs: dict[str, Any] = {"dom_snapshot": "", "ax": [], "screenshot": None}
    try:
        page.set_default_timeout(OBSERVE_TIMEOUT_MS)
    except Exception:
        pass  # fake pages / no such method
    try:
        txt = html = ""
        if not getattr(page, "_dom_too_slow", False):
            t0 = time.monotonic()
            txt = browser_actions._bounded_eval(
                page, "() => document.body ? document.body.innerText : ''",
                timeout_ms=OBSERVE_TIMEOUT_MS, default="") or ""
            if not txt:
                # NOT page.content(): it takes no timeout and blocks on the main
                # thread (21.8s measured on a pegged page). This is the same
                # serialization, submitted through the bounded path. Only tried
                # when innerText came back empty — on a healthy page that is the
                # cheap common case, and on a wedged one it would just pay the
                # cap twice.
                html = browser_actions._bounded_eval(
                    page, "() => document.documentElement ? "
                          "document.documentElement.outerHTML : ''",
                    timeout_ms=OBSERVE_TIMEOUT_MS, default="") or ""
            if time.monotonic() - t0 > OBSERVE_TIMEOUT_MS / 1000.0:
                try:
                    page._dom_too_slow = True
                except Exception:  # noqa: BLE001 - can't annotate; accept the cost
                    pass
        obs["dom_snapshot"] = (txt or html or "")[:6000]
    except Exception:
        pass
    # The accessibility tree is the state evidence the judge actually reasons
    # over: innerText cannot show a modal opening, an aria-expanded flip, or a
    # slider's valuenow moving, and the judge repeatedly read that absence as
    # "the DOM remained unchanged" for interactions that plainly worked.
    #
    # DEFECT 114 (extended). AX capture uses CDP `Accessibility.getFullAXTree`,
    # which — exactly like the CDP screenshot — takes no timeout and is
    # thread-affine, so a single call cannot be cut off. Its own `_ax_too_slow`
    # strike bounds ACCUMULATION (one slow call per task) but not the cost of that
    # one call: on the real pegged artifact `…visualeffe__P979t5f` the first
    # `getFullAXTree` alone ran ~170s — the strike then latches and every later
    # observe skips it, but the one call still dominated a 187s observe. The DOM
    # read runs FIRST and is genuinely bounded (`_bounded_eval` routes through
    # `locator(':root').evaluate(timeout=)`, which IS honoured against a pegged
    # main thread). So if that bounded read already timed out — `_dom_too_slow`
    # set — the renderer is saturated and the unboundable AX call will wedge on
    # it too. Skip it: the frame is what a pegged WebGL page has to offer anyway,
    # and this turns the worst-case first observe from ~187s into ~16s. On a
    # healthy page the DOM read is fast, the flag stays clear, and AX runs
    # normally — this only fires on a page that has already proven hostile.
    if getattr(page, "_dom_too_slow", False):
        try:
            page._ax_too_slow = True  # latch so downstream diffing stays consistent
        except Exception:  # noqa: BLE001 - stub page that rejects attributes
            pass
        obs["ax"] = []
    else:
        obs["ax"] = axtree.capture(page)
    try:
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        # DEFECT 51: every observe() mkstemps a PNG, `_persist` COPIES it into
        # frames/, and nothing ever unlinked the original. One step leaks one
        # file; a 60-step task leaks 60; the b0811 sweep left 128,438 stray
        # /tmp/tmp*.png totalling 65 GB and drove the filesystem to 83% full.
        # Unlinking inside `_persist` is wrong -- one observation is persisted
        # TWICE (the step frame and the item's `_after` frame), so the second
        # copy would find the source gone. Instead the paths ride on the page,
        # which scopes them to exactly one task, and `pipeline.verify_task` sweeps
        # them in a finally -- there rather than at the end of `run_trajectory`
        # because the PLANNER's initial observation is taken before the trajectory
        # starts. Never raises: a leaked temp is better than a lost run.
        try:
            page._shot_tmps.append(tmp)
        except AttributeError:
            try:
                page._shot_tmps = [tmp]
            except Exception:  # noqa: BLE001 - stub page that rejects attributes
                pass
        except Exception:  # noqa: BLE001
            pass
        obs["screenshot"] = _observe_screenshot(page, tmp)
    except Exception:
        obs["screenshot"] = None
    return obs


def _observe_screenshot(page: Any, tmp: str) -> str | None:
    """Capture one observe() frame, bounded against a pegged renderer.

    DEFECT 114. Neither screenshot path can be trusted with a `timeout=` argument
    on a continuously-animating canvas, and neither can be hard-bounded from here:

      * `page.screenshot(path=tmp, timeout=OBSERVE_TIMEOUT_MS)` does NOT honour its
        timeout — Playwright auto-waits for a "stable" frame, and that stabilization
        poll only checks the deadline between cycles, cycles a rAF/WebGL-pegged main
        thread starves. py-spy of the b0811 s01/s05 tail caught both worker threads
        parked in this exact call for a full 40-min shard (2 verdicts of 149) before
        the driver stall-killed it; the tail could never drain because every
        visualization shard wedged here forever.
      * `_cdp_screenshot` (`Page.captureScreenshot`) is NOT the fast, safe bound its
        docstring claimed. Measured on the pegged `miniapp_visualization` page it
        wedged just as hard: iter 0 returned in 25.77s, iter 1 never returned
        (py-spy: main thread parked in the CDP send at 3min+). CDP takes no timeout
        (verified) and the sync API is thread-affine, so this call cannot be cut off
        from a helper thread either.

    The two existing safety nets both MISS this wedge, and understanding why fixes
    it. The `_ax_too_slow`/`_dom_too_slow` strike caps a *returning-but-slow* call to
    once per task — useless when the first call never returns. The slot-reaper
    (DEFECT 109) kills a thread wedged against a browser that no longer EXISTS —
    but here the browser is alive and painting at 100% CPU; it just never produces a
    stable frame, so the reaper's liveness limb reports it healthy and never fires.
    A live browser wedged in a capture that never returns falls in the gap between
    them.

    Since the call cannot be bounded, the frame must not be ATTEMPTED on a page
    already known to be renderer-hostile. observe() reads the DOM (`_dom_too_slow`)
    and the AX tree (`_ax_too_slow`) BEFORE it reaches the screenshot, and a page
    that pegs the renderer trips at least one of those on the very first observe —
    before the screenshot can wedge. So we gate on those flags: a renderer-hostile
    page skips the frame entirely (AX + DOM carry the grading; a saturated WebGL
    canvas offers little a frame can add). This is the same accumulation-cap
    philosophy as the AX strike, but keyed on a signal that is already set before
    the hang can happen rather than one that requires the hang to complete first.

    On a healthy page CDP is PRIMARY: `Page.captureScreenshot` reads the current
    compositor frame with no stabilization wait, which is exactly the evidence a
    verifier wants and is produced even by a mildly-animating page that the
    stabilization gate would reject. `_shot_too_slow` remains a belt-and-braces
    strike for a page that is slow-but-returning yet did not trip the DOM/AX limbs
    (e.g. a heavy raster that the main-thread reads happened to answer inside 8s)."""
    # A page whose renderer already refused a DOM read or an AX capture inside the
    # budget this observe is saturated: the screenshot would be the call that
    # wedges (it needs the same main thread the DOM read just timed out on), and
    # unlike those two it may never return. Skip the frame rather than gamble the
    # whole shard on it. `_shot_too_slow` is set once a capture that DID return
    # overran, for the rarer page that saturates only the compositor.
    if (getattr(page, "_dom_too_slow", False)
            or getattr(page, "_ax_too_slow", False)
            or getattr(page, "_shot_too_slow", False)):
        return None
    # Stub/fake pages in the smokes have no CDP session; they accept only
    # screenshot(path=...). `_cdp_screenshot` returns False for them (no
    # `page.context.new_cdp_session`), so fall through to the plain Playwright call.
    t0 = time.monotonic()
    if _cdp_screenshot(page, tmp):
        if time.monotonic() - t0 > OBSERVE_TIMEOUT_MS / 1000.0:
            _flag_shot_too_slow(page)  # returned, but slow: do not re-pay it
        return tmp
    t0 = time.monotonic()
    try:
        page.screenshot(path=tmp, timeout=OBSERVE_TIMEOUT_MS)
        got = tmp
    except TypeError:
        # Stub/fake pages (smoke tests) accept screenshot(path=...) only.
        page.screenshot(path=tmp)
        return tmp
    except Exception:  # noqa: BLE001 - stabilization gate never opened
        got = None
    if time.monotonic() - t0 > OBSERVE_TIMEOUT_MS / 1000.0:
        _flag_shot_too_slow(page)
    return got


def _flag_shot_too_slow(page: Any) -> None:
    """Mark a page whose screenshot overran, so later observes skip the frame."""
    try:
        page._shot_too_slow = True
    except Exception:  # noqa: BLE001 - stub page that rejects attributes
        pass


def _sweep_shot_tmps(page: Any) -> int:
    """Unlink the observe() temp PNGs this page accumulated. Never raises."""
    try:
        tmps = list(getattr(page, "_shot_tmps", ()) or ())
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for p in tmps:
        try:
            os.unlink(p)
            n += 1
        except OSError:
            pass
    try:
        page._shot_tmps = []
    except Exception:  # noqa: BLE001
        pass
    return n


# --- Build-defect detection ------------------------------------------------
#
# Some artifacts render "successfully" (pixels paint, no page error) yet are
# functionally broken in a way a checklist judge scores as a plain low score,
# muddying the training signal. Two classes recur in the b0811 game testbed and
# are cheaply detectable at load time; we surface them as structured
# `build_defects` on the trajectory (annotate-only — the functional score is
# left untouched; see pipeline.verify_task). A defect entry is:
#     {"kind": <slug>, "detail": <human-readable one-liner>}
#
# CLASS 1 — forced-visible overlay (CSS specificity bug). The dominant defect:
# an `#id{display:grid}` rule (specificity 1,0,0) overrides the UA stylesheet's
# `[hidden]{display:none}` (0,1,0), so an element the HTML marks `hidden` paints
# anyway. When that element is a full-viewport error/fallback sheet (a NO-WEBGL
# "nofx", a "fatal"/"crash" panel, a "rotate your device" gate) it permanently
# covers a working game. The RUNTIME signal is unambiguous and needs no id
# allowlist: if JS had legitimately revealed a HUD it would have *removed* the
# `hidden` attribute (or set inline `display`), so an element that STILL carries
# `hidden` yet computes to display!=none and occupies a large share of the
# viewport is exactly this bug — never a normally-toggled HUD. (Contrast the
# static scanner _scan_overlay_bug.py, which over-flags toggled HUDs precisely
# because it can't see the runtime attribute state.)

# Fraction of the viewport a forced-visible `[hidden]` element must cover to be
# judged a screen-covering overlay rather than an incidental stray node. A
# NO-WEBGL/fatal sheet is `position:absolute; inset:0` (100%); a stray mislaid
# badge is tiny. 25% cleanly separates the two without an id allowlist.
_OVERLAY_MIN_AREA_FRAC = 0.25

# JS run against the live page: return every element that carries a bare
# `hidden` attribute yet computes to display!=none, with the fraction of the
# viewport its box covers. Pure read — no mutation. Guarded entirely on the JS
# side so a stub page (no querySelectorAll) simply yields [].
_OVERLAY_PROBE_JS = """() => {
  try {
    const vw = window.innerWidth || 1, vh = window.innerHeight || 1;
    const vp = vw * vh;
    const out = [];
    document.querySelectorAll('[hidden]').forEach(el => {
      const cs = getComputedStyle(el);
      if (cs.display === 'none') return;              // correctly hidden — skip
      const r = el.getBoundingClientRect();
      out.push({
        id: el.id || ('<' + el.tagName.toLowerCase() + '>'),
        display: cs.display,
        areaFrac: (r.width * r.height) / vp,
        z: cs.zIndex,
      });
    });
    return out;
  } catch (e) { return []; }
}"""


def detect_overlay_defects(page: Any) -> list[dict[str, Any]]:
    """Runtime forced-visible-overlay detector (build-defect CLASS 1).

    Enumerate elements that STILL carry the `hidden` attribute yet compute to
    display!=none; report any that cover >= `_OVERLAY_MIN_AREA_FRAC` of the
    viewport as a screen-covering overlay. Best-effort: any probe failure (stub
    page, evaluate unsupported) returns [] so detection never sinks a task."""
    try:
        rows = _bounded_page_eval(page, _OVERLAY_PROBE_JS, default=None)
    except Exception:
        return []
    if not isinstance(rows, list):  # stub page returned innerText, not the probe list
        return []
    defects: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            frac = float(r.get("areaFrac") or 0.0)
        except (TypeError, ValueError):
            frac = 0.0
        if frac >= _OVERLAY_MIN_AREA_FRAC:
            defects.append({
                "kind": "forced-visible-overlay",
                "detail": (f"element #{r.get('id')} carries [hidden] yet computes "
                           f"display:{r.get('display')} and covers {frac:.0%} of the "
                           f"viewport (CSS specificity bug: an #id display rule "
                           f"overrides [hidden]{{display:none}}), permanently "
                           f"covering the page."),
            })
    return defects


# ── Entry gate (build-defect CLASS 4 / harness rescue) ───────────────────────
# DEFECT 43. The single largest cause of lost evidence in the b0811 v011 sweep.
# Measured over 1,702 finished tasks: 2,471 clicks were rejected with
# "<el> intercepts pointer events", 759 of them by an element called `#boot`,
# and the covering element was one of #boot/#gate/#loader/#cover/#curtain/#scrim
# in 69 tasks of which 63 scored exactly 0.000 and 44 recorded NO build defect
# at all — so selection could not tell a dead artifact from a badly-verified one.
#
# Two OPPOSITE causes wear the same costume, and telling them apart is the whole
# job here:
#
#   (a) A GESTURE GATE. Web Audio and pointer-lock cannot start without a user
#       gesture, so the artifact correctly ships a "click to begin" sheet. It is
#       dismissible, and every check behind it is verifiable once it is gone.
#       `advance` does NOT clear these: it scores the highest forward CTA on the
#       whole page, and on b0811 `QAmsEw4` that was the console's "PLAY" button
#       BEHIND the gate — force-clicked three times, reported ok each time,
#       while `#gate` still intercepted every later click. Scoring the page
#       globally cannot fix this; the click has to be aimed INSIDE the covering
#       element.
#
#   (b) A DEAD SPLASH. The boot overlay's own removal code never ran (a module
#       failed to load, a shader failed to compile), so the artifact is a black
#       rectangle forever. b0811 `bgDNi7V`: `#boot` covering everything, blank
#       000_initial.png AND blank desktop.png, `renderable: True`,
#       `build_defects: []`, score 0.0 with gate_reason "no determinate
#       evidence". That is a real build defect being reported as a verification
#       failure.
#
# The discriminator is simply WHETHER CLICKING IT WORKS. So: find what covers the
# viewport centre, click the most plausible control inside it, and look again.
# Gone -> case (a), rescued, and the run proceeds with the gate lifted. Still
# there -> case (b), and we record `entry-gate-stuck` so selection can drop the
# artifact for the right reason instead of blaming the verifier.
#
# Why this is safe to do unconditionally: an artifact with no covering element
# yields no candidate and the whole routine is a no-op. We only ever click an
# element that is ALREADY intercepting the page's own centre, which no working
# artifact does with content the user is meant to interact with.

# Fraction of the viewport the covering element must span before we treat it as
# an entry gate rather than an ordinary widget under the cursor. Deliberately
# higher than _OVERLAY_MIN_AREA_FRAC: this routine CLICKS what it finds, so it
# must be a screen-blocker, not a centred card on a landing page.
_GATE_MIN_AREA_FRAC = 0.55

# DEFECT 83 -- how many pressable controls a covering element may hold before we
# refuse to treat it as an entry gate. A gate is a doorway: one way through
# ("PLAY", "Tap anywhere", "I understand"), or none at all when it is the dead
# splash this routine exists to detect. An artifact's own full-screen MENU is
# not a doorway, and clicking "the largest button inside it" is not dismissal --
# it is a random interaction with the artifact's real UI.
#
# Measured on the live claude-opus-5 run, `task_0001`: `#menu` is the race-setup
# screen (position:fixed, 100% of the viewport, nine buttons -- 1-SOLO,
# 2-SPLIT SCREEN, 4/6/8, 2/3/5, START RACE, GARAGE). The probe took it for a
# gate and clicked its LARGEST button, "2 - SPLIT SCREEN", three times. The menu
# of course stayed up -- a player-count toggle does not dismiss a menu -- so we
# recorded `entry-gate-stuck` ("the artifact's own boot/gate teardown never ran")
# against an artifact that was working perfectly. Worse than the false defect was
# the side effect: those three clicks SELECTED split-screen, and the judge's
# rationale for the critical item reads "The game is currently in split-screen
# mode ... instead of solo mode" -- the verifier manufactured the state it then
# graded the artifact down for.
#
# Four, not two: a start screen may legitimately carry a difficulty triple plus
# its START, and clicking the largest of those is the same mistake in miniature.
# Both cases this routine was built for survive the guard untouched (a dead
# splash has 0 controls; a "click to begin" sheet has 1).
_GATE_MAX_CONTROLS = 4

# Return the element covering the viewport centre (walking up to the ancestor
# that actually spans the screen), plus the best clickable control inside it.
# Pure read except for the marker attribute, which is how the caller clicks the
# candidate by a stable selector. Guarded entirely JS-side so a stub page yields
# null and the caller no-ops.
_GATE_PROBE_JS = """() => {
  try {
    const vw = innerWidth || 1, vh = innerHeight || 1, vp = vw * vh;
    let el = document.elementFromPoint(Math.floor(vw / 2), Math.floor(vh / 2));
    if (!el) return null;
    // Walk up to the outermost ancestor that still covers the viewport: the hit
    // is usually a <p> or <div> INSIDE the sheet (Playwright's own message says
    // '<p class="ob-lede"> from <div id="onboard">'), and the sheet is what we
    // must judge and dismiss.
    //
    // Only POSITIONED ancestors qualify. A gate is painted on top, i.e. fixed,
    // absolute or sticky; `<body>` and a hero <section> are static, in-flow
    // content that merely happens to be viewport-sized. Without this filter the
    // walk runs straight past the real gate to `<body>` (`height:100vh` on any
    // full-screen app), which is static — so the probe reported "no gate" on
    // precisely the pages it exists for, and clicking a static coverer would be
    // a random interaction with the artifact's real content.
    let cover = null;
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const p = getComputedStyle(e).position;
      if (p !== 'fixed' && p !== 'absolute' && p !== 'sticky') continue;
      const r = e.getBoundingClientRect();
      if ((r.width * r.height) / vp >= %(frac)s) cover = e;
    }
    if (!cover) return null;
    // The artifact's own canvas is the thing we came to interact with, never a
    // gate — a fixed full-viewport <canvas> is the normal WebGL case.
    if (cover.tagName === 'CANVAS') return null;
    // Best control INSIDE the cover. Prefer a real button, then anything that
    // looks pressable; among those prefer the largest, which is the CTA rather
    // than a close 'x' in the corner. We do NOT require a forward keyword:
    // "01 Play built-in score", "Tap anywhere", "I understand" and an unlabelled
    // <div class="gate__hit"> are all legitimate, and a keyword list is exactly
    // what made `advance` pick the wrong button on the page behind the gate.
    let best = null, bestArea = -1;
    const cands = cover.querySelectorAll(
      'button,a[href],[role="button"],input[type="submit"],input[type="button"],' +
      '[onclick],[class*="btn"],[class*="start"],[class*="begin"],[class*="enter"]');
    cands.forEach(c => {
      const r = c.getBoundingClientRect();
      const s = getComputedStyle(c);
      if (r.width < 8 || r.height < 8) return;
      if (s.visibility === 'hidden' || s.display === 'none') return;
      if (c.disabled) return;
      const a = r.width * r.height;
      if (a > bestArea) { bestArea = a; best = c; }
    });
    // How MANY pressable controls the coverer holds. A boot gate offers one
    // way forward ("click to begin", "I understand") or none at all (a dead
    // splash); an artifact's own full-screen MENU offers a screenful. The
    // caller uses this to tell the two apart before it clicks anything --
    // see _GATE_MAX_CONTROLS.
    let nControls = 0;
    cands.forEach(c => {
      const r = c.getBoundingClientRect();
      const s = getComputedStyle(c);
      if (r.width < 8 || r.height < 8) return;
      if (s.visibility === 'hidden' || s.display === 'none') return;
      if (c.disabled) return;
      nControls += 1;
    });
    // No control at all: the sheet itself may take the click ("click anywhere
    // to start" is usually a listener on the sheet). Clicking the cover is
    // harmless either way — it is already swallowing every click we make.
    const target = best || cover;
    target.setAttribute('data-iv-gate', '1');
    return {
      cover: cover.id ? '#' + cover.id
                      : cover.tagName.toLowerCase() + '.' +
                        String(cover.className || '').slice(0, 24),
      label: ((target.innerText || target.value || '') + '').trim().slice(0, 60),
      had_control: !!best,
      n_controls: nControls,
      areaFrac: (() => { const r = cover.getBoundingClientRect();
                         return (r.width * r.height) / vp; })(),
    };
  } catch (e) { return null; }
}""" % {"frac": _GATE_MIN_AREA_FRAC}


def dismiss_entry_gate(page: Any, *, tries: int = 3) -> list[dict[str, Any]]:
    """Click through a full-screen entry gate; report one if it will not lift.

    Returns a (possibly empty) list of build-defect dicts — `entry-gate-stuck`
    when a screen-covering overlay survived every attempt, which means the
    artifact is dead rather than merely gated. Never raises: every probe is
    guarded, and any failure degrades to "no gate found", which is exactly the
    behaviour before this existed.

    `tries` > 1 because gates legitimately stack: a cookie/rotate notice over a
    "click to start" sheet is two clicks, and an audio app may re-gate after its
    context resumes. Each pass re-probes from the centre, so a second gate is
    found on its own terms rather than assumed.
    """
    defects: list[dict[str, Any]] = []

    def _probe() -> dict[str, Any] | None:
        # DEFECT (b0811 `miniapp_interactive-art_generati__a2YwBBP`): bare
        # `page.evaluate` accepts NO timeout and `set_default_timeout` does NOT
        # apply to it — on a renderer that keeps its main thread pegged by
        # continuous rAF/WebGL/WebGPU work, this call parks forever. Greenlet
        # dump caught the worker parked at exactly this line during b0811 execute
        # after the install_fps_counter bound was added. Route through
        # `browser_actions._bounded_eval` (in-greenlet `Locator(":root").evaluate`
        # which DOES honour its timeout) so a wedged renderer degrades to "no
        # gate found" — the same path a stub page / closed context already takes.
        hit = browser_actions._bounded_eval(page, _GATE_PROBE_JS, default=None)
        return hit if isinstance(hit, dict) else None

    for _ in range(max(1, tries)):
        hit = _probe()
        if hit is not None and int(hit.get("n_controls") or 0) >= _GATE_MAX_CONTROLS:
            # The artifact's own screen, not a gate (DEFECT 83). Touch nothing:
            # no click (it would mutate real app state), and no defect (the
            # artifact is not dead -- it is showing us its menu). The plan's own
            # steps interact with this screen next, which is the correct owner
            # of those clicks.
            return defects
        if hit is None:
            # Nothing covers the centre. On the first pass that is an ordinary
            # ungated page; on a later pass it means the gate we just clicked is
            # gone. Both are case (a) — success, and no defect.
            return defects
        # force=True for the same reason `_action_advance` uses it: these sheets
        # animate (a pulsing "click to begin"), so Playwright's stability gate
        # never settles even though the click would land. A native dispatch
        # follows because force=True still hit-tests, and the gate's own decorative
        # child may sit on top of its button.
        try:
            page.click("[data-iv-gate]", timeout=2000, force=True)
        except Exception:
            pass
        try:
            page.dispatch_event("[data-iv-gate]", "click", timeout=1500)
        except Exception:
            pass
        # Same DEFECT-120 hazard as `_probe` above: bare page.evaluate wedges on
        # a renderer-pegged page. Route through the bounded path; if the marker
        # cleanup times out the marker leaks (harmless — the next pass re-tags).
        browser_actions._bounded_eval(
            page,
            "() => document.querySelectorAll('[data-iv-gate]')"
            ".forEach(e => e.removeAttribute('data-iv-gate'))",
            default=None)
        # Give the gate's transition (and any audio-context resume) time to run
        # before the next pass judges whether it lifted.
        try:
            page.wait_for_timeout(500)
        except Exception:
            pass
    # Out of attempts. Re-probe rather than trusting the last pre-click state:
    # the final click may well have worked, and reporting a defect on an artifact
    # that did open is the worst outcome here.
    final = _probe()
    if final is None:
        return defects
    defects.append({
        "kind": "entry-gate-stuck",
        "detail": (f"{final.get('cover')} covers {float(final.get('areaFrac') or 0):.0%} "
                   f"of the viewport and survived {max(1, tries)} dismissal attempts "
                   f"(control: {final.get('label') or '<none>'!r}); the artifact's "
                   "own boot/gate teardown never ran, so every interaction "
                   "below it is unreachable."),
    })
    return defects


def detect_cdn_defects(failed_requests: list[dict[str, str]], entry_url: str) -> list[dict[str, Any]]:
    """Unreachable external-dependency detector (build-defect CLASS 2).

    A game that streams its engine (e.g. three.js) from a CDN the sandbox can't
    reach shows a "No uplink"/fatal panel over a blank canvas — renders, but is
    dead. We flag when a cross-origin resource request FAILED to load. Requests
    to the artifact's own origin (its bundled assets) are ignored: only a
    genuinely external, unreachable dependency is a build defect here."""
    try:
        host = urlsplit(entry_url).hostname or ""
    except Exception:
        host = ""
    seen: set[str] = set()
    defects: list[dict[str, Any]] = []
    for req in failed_requests:
        url = req.get("url") or ""
        try:
            rhost = urlsplit(url).hostname or ""
        except Exception:
            rhost = ""
        # cross-origin only: same-host failures are the artifact's own bug the
        # functional checklist already covers, not an unreachable dependency.
        if not rhost or rhost == host:
            continue
        if rhost in seen:
            continue
        seen.add(rhost)
        defects.append({
            "kind": "cdn-unreachable",
            "detail": (f"external dependency from {rhost} failed to load "
                       f"({req.get('failure') or 'request failed'}); the artifact "
                       f"streams a dependency the sandbox cannot reach."),
        })
    return defects


# Resource types whose absence stops the artifact rather than degrading it. A
# missing decorative image leaves a working page with a broken thumbnail — that
# IS bad authoring and the functional checklist should score it. A missing
# script or stylesheet means the shipped bundle is incomplete, which is a
# different fact about the artifact and the one selection needs to see.
_LOADBEARING_RESOURCE_TYPES = frozenset({"script", "stylesheet"})


def detect_missing_asset_defects(bad_responses: list[dict[str, Any]],
                                 entry_url: str) -> list[dict[str, Any]]:
    """Incomplete-bundle detector (build-defect CLASS 3).

    The mirror image of ``detect_cdn_defects``: SAME-origin, load-bearing assets
    the artifact references but never shipped. b0811
    `miniapp_interactive-art_atmosphe__HEKgD2h` is the case that motivated it —
    `index.html` requests its own `app.js` and `shaders.js`, both 404, so the
    page paints its author-written "RIG OFFLINE / Failed to load app.js" sheet.
    Everything downstream then behaves correctly and uselessly: `renderable` is
    True (a sheet is pixels), 11 of 12 items are determinate, every one of them
    fails with an accurate rationale, and the verdict is 0.0 with
    `build_defects: []`. Nothing in that record distinguishes "the generator
    shipped an incomplete bundle" from "the model wrote a bad app", and those
    are not the same training signal.

    Why this is a separate detector and not a relaxation of the cross-origin
    filter in ``detect_cdn_defects``: that filter is right. Most same-origin
    misses are ordinary authoring bugs (a mislaid sprite, a wrong path to a
    font) that the functional checklist already prices in, and flagging them all
    would bury the CDN signal. The narrow case worth its own kind is a
    same-origin **script or stylesheet** — without those the artifact cannot run
    at all, so its score says nothing about the code that was written.

    Why it needs its own capture: a 404 is a COMPLETED response, not a failed
    request, so it never fires `requestfailed` and is invisible to the
    `failed_requests` list CLASS 2 reads. The only evidence in the old records
    was two anonymous `Failed to load resource: ... 404` console lines with no
    URL attached.

    Annotate-only, like the other two classes — the 0.0 stands. Each missing
    path is reported once."""
    try:
        host = urlsplit(entry_url).hostname or ""
    except Exception:
        host = ""
    seen: set[str] = set()
    defects: list[dict[str, Any]] = []
    for resp in bad_responses:
        url = str(resp.get("url") or "")
        rtype = str(resp.get("resource_type") or "")
        if rtype not in _LOADBEARING_RESOURCE_TYPES:
            continue
        try:
            parts = urlsplit(url)
        except Exception:
            continue
        if not parts.hostname or parts.hostname != host:
            continue          # cross-origin is CLASS 2's job
        path = parts.path or url
        if path in seen:
            continue
        seen.add(path)
        defects.append({
            "kind": "missing-own-asset",
            "detail": (f"the artifact requests its own {rtype} {path} and the "
                       f"server returned {resp.get('status')}; the shipped bundle "
                       f"is incomplete, so the page cannot run and its score "
                       f"reflects the missing file rather than the code written."),
        })
    return defects


# Actions that resolve a CSS/text selector and auto-wait for it. These are the
# ones that hang for the full Playwright default timeout when the selector never
# matches, so the executor caps them and pre-checks selector presence.
# jQuery's `:contains("X")` is NOT a CSS selector, and Playwright rejects the
# whole selector with a hard `SyntaxError: 'button:contains("Day")' is not a
# valid selector`. The planner emits it anyway (it is the single most familiar
# "match by text" idiom in the training data) — 14 steps on the b0810 pilot died
# this way, each one a lost interaction, not a broken artifact. Playwright's own
# equivalent is `:has-text("X")`, so translate rather than discard.
_CONTAINS_RE = re.compile(r""":contains\(\s*(["'])(.*?)\1\s*\)""")

# Accessibility-tree phrasing — `button 'RESET'`, `slider "BUBBLE SCALE"`,
# `option 'China'`. Reads like a locator but is not one: Playwright parses the
# leading word as a tag name and then chokes on the quote (`Unexpected token
# ""RESET"" while parsing css selector`). The intent is unambiguous, so rewrite
# to the role+text form the DOM actually exposes.
_ROLE_TEXT_RE = re.compile(r"""^\s*(?P<role>[a-z]+)\s+(?P<q>["'])(?P<txt>.+?)\2\s*$""")

# ARIA role -> the CSS the artifacts actually use. Artifacts here are hand-written
# HTML, so a "switch"/"checkbox" is a real `<input type=checkbox>` and a "slider"
# is `<input type=range>`; roles with no clean CSS equivalent fall back to
# `[role=...]` plus the text predicate.
_ROLE_CSS = {
    "button": "button, [role=button]",
    "link": "a, [role=link]",
    "slider": "input[type=range], [role=slider]",
    "switch": "input[type=checkbox], [role=switch]",
    "checkbox": "input[type=checkbox], [role=checkbox]",
    "radio": "input[type=radio], [role=radio]",
    "option": "option, [role=option]",
    "combobox": "select, [role=combobox]",
    "tab": "[role=tab], button",
    # Every text-like `<input>`, not just `type=text`. Written as one
    # comma-free compound on purpose: the wrapper chain below takes
    # `css.split(",")[0]` as the control it descends to, so a comma-separated
    # list of input types would silently narrow back to the first one — which is
    # how `password input` (32 steps in the b0811 census) resolved to nothing on
    # a page whose field is `type=password`.
    "textbox": ("input:not([type=range]):not([type=checkbox]):not([type=radio])"
                ":not([type=button]):not([type=submit]):not([type=reset])"
                ":not([type=file]):not([type=color]), textarea, [role=textbox]"),
}

# Tags the planner names as if they were roles — `canvas 'Interactive desert
# oasis simulation'` — where the quoted string is the accessible name rather
# than the element's text. Kept to interactive/graphical tags on purpose: an
# unrecognised leading word must stay unrecognised (see normalize_selector).
_AX_TAGS = frozenset({"canvas", "svg", "video", "audio", "iframe", "img",
                      "select", "input", "textarea", "button", "a"})


# DEFECT 69: the checklist generator names controls in PROSE, in two grammars
# that no rewrite above recognises, and both are ~100% fatal. Census of every
# executed selector step on b0811/full (4,111 tasks), grouped by what was
# authored:
#
#   family                             ok      err     err%
#   real selector (`button:has-text`) 14001   10482    42.8%
#   tag-only CSS (`canvas`, `body`)    2070    1062    33.9%
#   AX dialect (`slider 'WIND'`)          8     101    92.7%
#   REVERSED (`AIRFOIL button`)           0     182   100.0%   <- this defect
#   BARE LABEL (`START RUN`)             24     780    97.0%   <- this defect
#   pseudo (`window`, `viewport`)         1      59    98.3%
#
# The two prose families are 3.7% and 17.2% of the 56,507 authored targets
# (2,093 across 247 tasks, and 9,704 across 924 tasks). Nothing succeeds by
# accident: the string reaches Playwright as a tag name and matches nothing.
# Reproduce with `_measure_prose_targets.py [--errors] full`.
#
# This surfaced through DEFECT 66, which made it WORSE before it was found. On
# `mKoh6BY` the planner had covered items 2, 7, 10 and 11 with working selectors
# (`button:has-text("AIRFOIL")`); the adoption pass could not recognise them as
# the same control because `planner.target_label` reads "airfoil button" and
# "airfoil" as different labels, so it appended the authored prose instead and
# all four steps errored. Determinate items fell 9/12 -> 6/12 — a fix that added
# evidence removed it. So the two halves must land together: this rewrite makes
# the prose RUNNABLE, and the `target_label` change makes it ADOPTABLE so a
# working selector is preferred over a rewrite of the prose whenever one exists.
#
# Prose is canonicalised into the `role 'LABEL'` dialect the code above already
# handles, rather than compiled to CSS here. That is deliberate: the role branch
# carries hard-won detail (a slider's label lives on the wrapper, not the input;
# `combobox` must not use `:has-text` because a `<select>`'s text is its
# options), and a second compiler would drift from it.
_PROSE_ROLES = {
    "button": "button", "btn": "button",
    "checkbox": "checkbox", "check": "checkbox",
    "slider": "slider", "range": "slider",
    "link": "link",
    "switch": "switch", "toggle": "switch",
    "radio": "radio",
    "textbox": "textbox", "input": "textbox", "field": "textbox",
    "combobox": "combobox", "dropdown": "combobox", "select": "combobox",
    "tab": "tab",
    "option": "option",
}

# `AIRFOIL button`, `STREAMLINES checkbox`, `CLEAR TEST SECTION ⌫ button`, and
# `password input`. Non-greedy label plus a trailing role word, optionally
# plural. The label floor of 1 char keeps `s button` out of the label position;
# a bare role word with no label is family 4's job, below.
_REV_ROLE_RE = re.compile(
    r"^(?P<txt>.+?)\s+(?P<role>" + "|".join(sorted(_PROSE_ROLES, key=len,
                                                   reverse=True)) + r")s?$",
    re.IGNORECASE)

# Anything that makes a string CSS, a Playwright engine selector, or the AX
# dialect. Apostrophes and double quotes are in here on purpose: `slider 'WIND'`
# must reach `_ROLE_TEXT_RE` unmodified, and a label containing a quote is
# skipped rather than mis-escaped — a missed rewrite is recoverable, a wrong
# selector clicks the wrong control. `\.\w` is unanchored because a class can be
# welded to a tag: without it `div.panel input` parses as the reversed dialect
# (label `div.panel`, role `input`) and a structural selector becomes a text
# match. Real labels punctuate with ". " (dot-space), which this does not match.
_PROSE_BAD = re.compile(r"""["'#\[\]>:()=,*~+|]|\.\w|\s>>\s"""
                        r"""|^\s*(?:xpath|text|css|id|data-testid)\s*=""")

# Names for the WINDOW, not for a control (467 steps, 98.3% error). They belong
# to the coordinate verbs, which aim at the viewport by other means. A text
# rewrite here would be worse than the error it replaces: `:text("window")`
# quietly matches any element whose copy happens to contain the word, so a step
# that honestly failed would start reporting a click on a paragraph.
_PSEUDO_TARGETS = frozenset({
    "window", "viewport", "screen", "page", "document", "browser", "body",
    "the page", "the window", "whole page", "full page", "center", "centre",
    "canvas center", "canvas centre", "center of canvas", "middle",
    "center of the canvas", "anywhere", "the canvas", "background",
})

# A word that is a real HTML tag. `div button` and `body canvas` are VALID CSS
# descendant selectors (2,070 successful steps), so a target whose every word is
# a tag is never treated as prose.
_HTML_TAGS = frozenset("""a abbr address area article aside audio b base bdi bdo
blockquote body br button canvas caption cite code col colgroup data datalist dd
del details dfn dialog div dl dt em embed fieldset figcaption figure footer form
h1 h2 h3 h4 h5 h6 head header hgroup hr html i iframe img input ins kbd label
legend li link main map mark menu meta meter nav noscript object ol optgroup
option output p param picture pre progress q rp rt ruby s samp script section
select slot small source span strong style sub summary sup svg table tbody td
template textarea tfoot th thead time title tr track u ul var video wbr""".split())

# Where a bare LABEL can live. `:text()` matches the SMALLEST element carrying
# the text, which is what a person means by "the START RUN button", and it is
# CSS-union-compatible (unlike the `text=` engine) so it can sit beside the
# aria-label and title alternates that cover icon-only controls. The bare
# `:text()` tail is load-bearing: b0811's div-row game menus (DEFECT 60) have no
# interactive tag or role at all, only a JS listener, and dropping the tail
# leaves exactly those artifacts stuck on their title screen.
_PROSE_INTERACTIVE = ("button, [role=button], a, summary, label, input, select, "
                      "textarea, [role=tab], [role=link], [role=menuitem], "
                      "[role=checkbox], [role=switch], [role=option], [tabindex]")


def _canonicalize_prose(t: str) -> str:
    """Prose control name -> the `role 'LABEL'` dialect, or ``t`` unchanged.

    Only fires on a string with no selector syntax whose words are not all HTML
    tags. Returns the canonical form for the reversed dialect; a bare role word
    and a bare LABEL have no canonical form and are returned unchanged, for
    `_prose_role_css` and `_prose_label_css` to handle in that order.
    """
    s = t.strip()
    if not s or _PROSE_BAD.search(s) or s.lower() in _PSEUDO_TARGETS:
        return t
    words = s.split()
    if len(words) < 2 or all(w.lower() in _HTML_TAGS for w in words):
        return t
    m = _REV_ROLE_RE.match(s)
    if not m:
        return t
    role = _PROSE_ROLES[m.group("role").lower()]
    txt = m.group("txt").strip()
    if not txt:
        return t
    return f"{role} '{txt}'"


def _prose_role_css(t: str) -> str:
    """A bare role WORD -> that role's CSS, or ``t`` unchanged.

    `slider`, `textbox`, `toggle`, `dropdown` name a kind of control with no
    label at all (331 `slider`, 111 `textbox`, 36 `checkbox` on b0811). There is
    no text to match on, so hand back the role's CSS alone and let
    `_query_measurable`/`_pin_union` pick the measurable one. That is what "drag
    the slider" means on a one-slider panel; on a multi-slider panel it is still
    the honest reading of what was written, and it is the difference between
    measuring the first slider and measuring nothing.

    Not routed through the `role ''` canonical form, because `_ROLE_TEXT_RE`
    requires a non-empty quoted label — the empty form would silently decline
    and the string would reach Playwright as a tag name, which is the bug.
    """
    s = t.strip().lower()
    # `button`, `input`, `select`, `label`, `link`, `option`, `tab` are also HTML
    # tags, and as a bare word the tag reading is the better one: already valid
    # CSS, and it succeeds today.
    if not s or " " in s or s in _HTML_TAGS:
        return t
    role = _PROSE_ROLES.get(s)
    return _ROLE_CSS[role] if role and role in _ROLE_CSS else t


def _prose_label_css(t: str) -> str:
    """Bare visible LABEL -> a text/aria/title union, or ``t`` unchanged.

    The last prose family: `START RUN`, `FORGOT PASSWORD?`, `JACK IN`, `SET SAIL
    ►` — a label with no role word anywhere (10,178 steps, 961 tasks, 95.3%
    error). Runs only after every role-aware rewrite has declined.
    """
    s = t.strip()
    if not s or _PROSE_BAD.search(s) or s.lower() in _PSEUDO_TARGETS:
        return t
    words = s.split()
    if all(w.lower() in _HTML_TAGS for w in words):
        return t
    # A single word that is a real tag is already valid CSS. A single word that
    # is not is still a label ("PAUSE", "Serve"), so only the tag case declines.
    if len(words) == 1 and words[0].lower() in _HTML_TAGS:
        return t
    esc = s.replace("\\", "\\\\").replace('"', '\\"')
    return (f':is({_PROSE_INTERACTIVE}):text("{esc}"), '
            f'[aria-label="{esc}"], [title="{esc}"], :text("{esc}")')


def normalize_selector(target: str) -> str:
    """Rewrite planner selector dialects Playwright cannot parse.

    Both rewrites below turn a HARD SyntaxError — which aborts the action and
    records a step error the judge later reads as evidence the feature is broken —
    into a selector that at worst matches nothing. Returns ``target`` unchanged
    when neither pattern applies.
    """
    t = (target or "").strip()
    if not t:
        return target

    # DEFECT 69, first half: fold the REVERSED prose dialect (`AIRFOIL button`)
    # into the `role 'LABEL'` form the two branches below already compile, so
    # both grammars share one implementation and one set of hard-won details.
    t = _canonicalize_prose(t)

    # `button 'RESET'` -> `:is(button, [role=button]):has-text("RESET")`. A form
    # control carrying its label in an adjacent node (input[type=range]) has no
    # text of its own, so for those roles target the labelled wrapper's control
    # instead of demanding the text be inside the input.
    m = _ROLE_TEXT_RE.match(t)
    if m and m.group("role") in _ROLE_CSS:
        role, txt = m.group("role"), m.group("txt").strip()
        css = _ROLE_CSS[role]
        esc = txt.replace("\\", "\\\\").replace('"', '\\"')
        if role in ("slider", "switch", "checkbox", "radio", "textbox", "combobox"):
            # The visible text belongs to the label/row, not the input, so we must
            # find the innermost element containing BOTH the text and the control
            # and then descend. Matching on text alone lands on the bare <span>
            # holding the label (which has no input inside it), and matching on
            # the control alone ignores which one the planner meant.
            #
            # `combobox` belongs here and NOT in the plain `:has-text` branch
            # below, even though a `<select>` does contain text: the text it
            # contains is its OPTIONS. `select:has-text("Lock viewport to
            # device")` therefore matches only if some option is named that,
            # which it never is — the string is the control's LABEL. That exact
            # selector missed on `4ewUptv`, both setup steps timed out, and the
            # item's own action then measured the default option and gated a
            # 1.000 artifact to 0.000.
            inner = css.split(",")[0].strip()
            # ...and the ACCESSIBLE NAME is not always in the DOM text. The
            # planner writes the AX tree's name — "Extrusion height" — while the
            # panel renders `<label>Extrusion</label><input aria-label="Extrusion
            # height">`, so a text-only chain matches nothing and the step is
            # lost (measured: 4 drags on `Extrusion height`, 2 on `Bloom
            # strength`, 2 each on `WATER`/`SUN`). Offer the aria-label match as
            # a first alternative: it is where that exact string actually lives,
            # and the text chain stays as the fallback for controls without one.
            #
            # "Innermost wrapper" is expressed as `:has(> inner)` — a DIRECT
            # child — rather than the old `>> nth=-1 >>` chain, because
            # Playwright's `>>` chaining cannot appear inside a comma union.
            # Both forms pick the same element; the plain descendant form WITHOUT
            # `:has(>` does not — measured against a 3-slider panel it returns
            # the FIRST slider on the page regardless of the label, which would
            # silently drag the wrong control.
            return (f'{inner}[aria-label="{esc}"], '
                    f':has(> {inner}):has-text("{esc}") {inner}')
        return f':is({css}):has-text("{esc}")'

    # An AX phrase the role map has no CSS for — `canvas 'Interactive desert
    # oasis simulation'`, the single most common failing target. It is not CSS at
    # all: `query_selector` raises "Unexpected token", the box is never measured,
    # and the drag dies on `KeyError: 'from'` having done nothing. The role here
    # IS a tag name and the quoted string IS the accessible name, so aim at the
    # element carrying that name, then fall back to the bare tag — a page has one
    # `<canvas>` far more often than not, and hitting it is strictly better than
    # not interacting at all.
    #
    # Only for words that are ACTUALLY HTML tags. `widget 'FOO'` is neither a
    # role nor a tag, and rewriting it to `widget[aria-label="FOO"], widget`
    # would turn an honest no-match into a silent match-nothing selector that
    # looks like it resolved — the same fake-success failure mode as force-click.
    if m and m.group("role").lower() in _AX_TAGS:
        role, txt = m.group("role").lower(), m.group("txt").strip()
        esc = txt.replace("\\", "\\\\").replace('"', '\\"')
        return (f'{role}[aria-label="{esc}"], {role}[title="{esc}"], '
                f'{role}[role="{esc}"], {role}')

    # `button:contains("Day")` -> `button:has-text("Day")`, per-occurrence so a
    # comma-union of several such predicates is fully translated.
    if ":contains(" in t:
        t = _CONTAINS_RE.sub(lambda mm: f':has-text("{mm.group(2).strip()}")', t)

    # DEFECT 69, second half. Every role-aware rewrite has now declined, so what
    # is left is either real CSS or prose carrying no role at all. Both remaining
    # prose families are handled last, in the order that loses the least: a bare
    # role WORD (`slider`) becomes that role's CSS, and a bare LABEL (`START
    # RUN`) becomes a text/aria/title union. Placed after `:contains` so a
    # jQuery-flavoured selector is repaired as CSS rather than read as a label.
    t = _prose_role_css(t)
    t = _prose_label_css(t)
    return _drop_unparseable_alternates(_drop_degenerate_alternates(t))


# `has-text` is a SUBSTRING match, so `button:has-text("R")` matches every
# button whose label contains an R. The planner writes comma-unions as
# fallbacks — `button:has-text("RESET"), button:has-text("R")` — and Playwright
# resolves a comma-union to the FIRST MATCH IN DOCUMENT ORDER, not to the first
# clause that matches. So when a "PARAMS P" button precedes the real RESET in
# the DOM, the one-letter fallback wins and we click the wrong control while
# recording a successful "click RESET". b0811 `36LuAgk`: the reset probe
# produced `+ button "PARAMS P" pressed=true` and the item failed. 347 of 4,112
# tasks (8.4%) carried a clause this short.
#
# One- and two-character text clauses are therefore dropped from a union that
# also has a longer, more specific clause. A short clause ALONE is kept — some
# controls really are labeled "▶" or "+", and dropping it would leave an empty
# selector, which is a worse failure than an imprecise one.
_TEXT_CLAUSE_RE = re.compile(r':has-text\(\s*"((?:[^"\\]|\\.)*)"\s*\)')


def _drop_degenerate_alternates(t: str) -> str:
    if "," not in t or ":has-text(" not in t:
        return t
    parts = [p.strip() for p in t.split(",")]
    kept, dropped = [], []
    for p in parts:
        m = _TEXT_CLAUSE_RE.search(p)
        # A clause with no text predicate (a CSS/aria fallback) is specific by
        # construction and always survives.
        if m is not None and len(m.group(1).strip()) <= 2:
            dropped.append(p)
        else:
            kept.append(p)
    # All clauses were short: the label genuinely is a glyph. Keep the original.
    if not kept or not dropped:
        return t
    return ", ".join(kept)


# An AX phrase hiding at the tail of an otherwise valid CSS list. The planner
# writes a primary selector and an AX-phrase fallback in one string —
# `input[type="range"][aria-label="Wave intensity"], slider "Wave intensity"` —
# intending the second as a safety net. CSS list parsing is ALL-OR-NOTHING, so
# the unparseable tail takes the working head down with it: query_selector
# raises, the box is never measured, and the drag dies on `KeyError: 'from'`.
# The plan offering us a fallback is what makes the step fail.
#
# Measured on the synthetic reproduction: the full list raises "Unexpected
# token", the first alternative alone resolves to a real 129x16 box. So drop the
# clauses Playwright cannot parse and keep the ones it can. This is the widest
# single class in the gate rounds' `KeyError: 'from'` steps.
_AX_TAIL_RE = re.compile(r"""^[a-z]+\s+["'].+["']$""")


def _drop_unparseable_alternates(t: str) -> str:
    if "," not in t:
        return t
    parts = [p.strip() for p in t.split(",")]
    kept = [p for p in parts if p and not _AX_TAIL_RE.match(p)]
    # Every clause was an AX phrase: there is no valid CSS to fall back to, so
    # leave the string alone and let the caller's normal no-match path run. A
    # single-clause AX phrase is handled earlier, by the role rewrite.
    if not kept or len(kept) == len(parts):
        return t
    return ", ".join(kept)


# A descendant chain whose LAST compound is a bare id. `canvas #gl` is valid CSS
# and matches nothing — it asks for an element with id `gl` INSIDE a canvas,
# while the page's `<canvas id="gl">` IS the canvas. The planner writes this
# shape when it knows both the tag and the id and joins them with a space
# instead of nothing (b0811 `FGfZjZb`, 1 lost drag; bare `canvas` and `#gl` each
# resolve to the same 1280x720 box).
#
# Only an ID tail is salvaged, and that restriction is the whole safety
# argument: an id is unique by construction, so `#gl` cannot land on a
# different element than the author meant. Falling back to a tail like `button`
# (from `.panel > .row:nth-child(2) button`) would silently drag the first
# button on the page — trading a visible no-match for an invisible wrong click,
# which is the failure mode this file spends most of its length avoiding.
_ID_TAIL_RE = re.compile(r"^[^,]*\s(#[A-Za-z][\w-]*)$")

# FALLBACK cap only. `_scan_flags` measures every match in ONE page-side call,
# so the scan is normally unbounded; this bound applies solely on the degraded
# path (a stub page in the smokes, or a batch that throws), where each box costs
# a round-trip.
#
# DEFECT 73. It used to be the real cap, and 12 was sized for "a hidden twin or
# two" — the FGfZjZb shape this scan was built for. Multi-view SPAs are a
# different shape entirely: a slide deck keeps every view in the DOM, so on
# b0811 `BxrjKKx` after navigating to Station 11,
#
#     32 `input[type='range']` matched; measurable at indices [18]; cap 12
#     -> _query_measurable returned the hidden index-0 element, box=None
#     -> the drag died `KeyError: 'from'` with "[drag target not measurable]"
#
# and the cap, not the page, is what lost it. Reach on the b0811 sweep: 2,988
# steps (10.39%) across 1,140 tasks aim a bare unscoped selector inside a
# view-navigating task (2,438 of 4,111 tasks navigate between views); worst
# offenders `X3MYyRF` (19 steps), `aWuyx8x` (14), `vRUr892` (13).
#
# Raising the number was the wrong fix — any constant is wrong for some deck,
# and the cost it guards is real: 1,500 `div` matches cost 502ms measured one at
# a time. Measured page-side in one batch the same 1,500 cost 29ms, which is
# cheaper than ANY cap worth having and 6x the 5.4ms the old cap-12 loop spent
# to get the wrong answer. So the constant stops being a correctness knob.
_MEASURABLE_SCAN = 12

# One round-trip for the whole match list. `page.evaluate(js, els)` accepts a
# list of ElementHandles and hands the callback a real array, so the predicate
# runs once per element INSIDE the page instead of once per element across the
# CDP boundary. Verified to agree element-for-element with the per-element loop.
_BOX_FLAGS_JS = """els => els.map(el => {
  if (!el) return 0;
  const r = el.getBoundingClientRect();
  return (r.width > 0 && r.height > 0) ? 1 : 0;
})"""

# The same predicate for ONE element, for `_scan_flags`'s degraded path.
_BOX_ONE_JS = """el => {
  const r = el.getBoundingClientRect();
  return (r.width > 0 && r.height > 0);
}"""


def _scan_flags(page: Any, els: list, js: str, per_el: str) -> list[bool]:
    """Evaluate a boolean predicate over EVERY match, batched page-side.

    Falls back to the capped per-element loop when the batch is unavailable —
    a stub page whose `evaluate` takes no second argument, or a serialisation
    error. The fallback is why `_MEASURABLE_SCAN` still exists; it returns
    `False` for everything past the cap, which is exactly the pre-DEFECT-73
    behaviour and therefore never worse than what it replaces.
    """
    if not els:
        return []
    try:
        return [bool(f) for f in page.evaluate(js, els)]
    except Exception:  # noqa: BLE001 - stub page, detached handle, bad serialisation
        pass
    out: list[bool] = []
    for el in els[:_MEASURABLE_SCAN]:
        try:
            out.append(bool(el.evaluate(per_el)))
        except Exception:  # noqa: BLE001 - detached, or a stub without evaluate
            out.append(False)
    return out + [False] * (len(els) - len(out))

# DEFECT 44 — a comma union is a PRIORITY LIST to the planner and a SET to CSS.
#
# The planner writes its precise selector first and a broad safety net last:
#
#     div:has-text("ANGLE OF ATTACK") + div input[type='range'], input[type='range']
#
# Branch 1 matches exactly the intended slider. Branch 2 matches EVERY slider on
# the page. CSS unions them and resolves in DOCUMENT ORDER, so whichever slider
# comes first in the DOM wins — and on a control panel that is essentially never
# the one named. Measured against a 2-row panel: branch 1 alone -> `#aoa`
# (correct), the full union -> `#free`. The safety net does not catch a fall; it
# causes one, and it does so SILENTLY: the step records `result: "ok"` because a
# box really was measured and dragged.
#
# This is the defect behind three separate reports on the same sweep:
#   `mKoh6BY` item 7 — plan drags "ANGLE OF ATTACK", trace shows
#       `+ slider "Freestream velocity" focused=True`. Reported twice.
#   `xXbgiFY` items 2/3/6 — every `div:has-text(...) + div input[type="range"],
#       div:has-text(...)` slider probe on the task.
# The reviewer's note is exact: "the execution agent interacted with the
# Freestream velocity slider!"
#
# `_drop_degenerate_alternates` already fixed the narrow version of this (a
# one-character `:has-text("R")` fallback) but explicitly spares a clause with no
# text predicate as "specific by construction". `input[type='range']` disproves
# that premise: it is a TAG-AND-ATTRIBUTE selector, maximally unspecific, and the
# most common fallback the planner writes.
#
# The fix honors the authored order instead of trying to guess which branches are
# too broad: try each branch ALONE, left to right, and take the first that yields
# a measurable element. Every candidate still satisfies a selector the planner
# wrote, so this narrows the choice without ever inventing a new target — and when
# no branch matches, the union is tried whole so any behaviour that depended on
# CSS-level union semantics is preserved.
def _union_branches(selector: str) -> list[str]:
    """Split a comma union into branches, or [] when it is not safely splittable.

    Commas INSIDE a functional pseudo-class belong to that pseudo-class, not to
    the union: `:is(button, [role=button]):has-text("X")` is ONE branch, and
    `normalize_selector` emits exactly that shape. Splitting it naively yields
    `:is(button` — unparseable, and it would take the working selector down with
    it. So track paren depth and quotes, and bail out entirely on anything
    unbalanced rather than guess.
    """
    if "," not in selector:
        return []
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    for ch in selector:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
            if depth < 0:
                return []
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if depth != 0 or quote:
        return []
    parts.append("".join(buf))
    out = [p.strip() for p in parts if p.strip()]
    return out if len(out) > 1 else []


def _bounded_query_all(page: Any, selector: str) -> list:
    """`page.query_selector_all(selector)`, but BOUNDED — [] on timeout.

    DEFECT 120 (extended). `page.query_selector_all` takes no timeout, and its
    protocol round-trip is serviced by the renderer's main thread — so on a page
    doing continuous rAF/WebGL work it dead-waits for the animation loop, which
    never ends. Both b0811 resume lanes wedged here forever (`_first_measurable_
    one`, `_text_salvage`), producing zero verdicts until the 2400s stall-kill.

    The `_cdp_screenshot` daemon-thread trick CANNOT be used: the sync API is
    greenlet-affine, so calling `query_selector_all` from another thread raises
    `Cannot switch to a different thread` immediately (DEFECT 117). The screenshot
    path only works because it opens a FRESH CDP session inside the worker thread.

    So bound it IN-GREENLET the way `browser_actions._bounded_eval` bounds
    `page.evaluate`: route through a `:root` locator whose `evaluate_handle` DOES
    honour its timeout against a pegged main thread (measured: raises TimeoutError
    at 3.00s, versus a `query_selector_all`/`locator.all()` that never returns).
    `Array.from(document.querySelectorAll(sel))` preserves the DOCUMENT ORDER that
    `_first_measurable_one` and the `_pin_*` narrowers depend on, and the returned
    JSHandle array is unwrapped to real ElementHandles — usable by `_scan_flags`,
    `.bounding_box()`, and being passed back into a page-side `evaluate` — so
    every caller works unchanged whether it consumes the handles or just `len()`.

    On timeout we latch `page._ax_too_slow` (shared with the screenshot/axtree
    bounds) so the rest of this trajectory's selector scans short-circuit to []
    at once rather than each paying the full timeout. Returns [] on any failure,
    which every call site already treats as "no match" — never raises.
    """
    if getattr(page, "_ax_too_slow", False):
        return []
    if not hasattr(page, "locator"):
        # Fake/stub pages (smokes, web-UI page-likes) have no `locator`; fall back
        # to the raw call so their behaviour is byte-for-byte unchanged.
        try:
            return page.query_selector_all(selector)
        except Exception:  # noqa: BLE001 - stub without the API, or bad selector
            return []
    try:
        arr = page.locator(":root").evaluate_handle(
            "(_root, sel) => Array.from(document.querySelectorAll(sel))",
            selector, timeout=QSA_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        # ONLY a timeout means the renderer is wedged — latch so later scans in
        # this trajectory short-circuit instead of each paying the full timeout.
        try:
            page._ax_too_slow = True
        except Exception:  # noqa: BLE001
            pass
        return []
    except Exception:  # noqa: BLE001 - bad selector / detached; NOT a wedge.
        # A malformed selector raises the same class here that `query_selector_all`
        # did before, and every caller's `except` already maps that to []/no-op.
        # Do NOT latch: a bad selector is not a hostile renderer, and latching
        # would wrongly disable every later scan for the whole trajectory.
        return []
    out: list = []
    try:
        for v in arr.get_properties().values():
            el = v.as_element()
            if el is not None:
                out.append(el)
    except Exception:  # noqa: BLE001 - handle detached mid-unwrap
        out = []
    finally:
        try:
            arr.dispose()
        except Exception:  # noqa: BLE001
            pass
    return out


def _bounded_evaluate_handle(page: Any, js: str, arg: Any) -> Any:
    """`page.evaluate_handle(js, arg)`, BOUNDED — None on timeout.

    The handle-returning sibling of `_bounded_query_all`, for the salvage passes
    that run arbitrary DOM-walking JS and return an element. A raw
    `page.evaluate_handle` is unbounded and dead-waits on a pegged renderer exactly
    like `query_selector_all` (measured: `_text_salvage`'s `_LABEL_SALVAGE_JS` call
    never returned on a rAF-pegged page; the bounded form below raised at the
    timeout). `page.evaluate_handle` takes no `timeout=`, so route through a `:root`
    locator whose `evaluate_handle` DOES honour one. That locator calls the function
    as `fn(rootElement, arg)`, but the salvage JS is written `fn(arg)` — so wrap it
    in a shim that drops the injected root element and forwards `arg` unchanged.

    Returns the raw JSHandle (caller does `.as_element()`), or None on any failure.
    Latches `page._ax_too_slow` on timeout, mirroring `_bounded_query_all`."""
    if getattr(page, "_ax_too_slow", False):
        return None
    if not hasattr(page, "locator"):
        try:
            return page.evaluate_handle(js, arg)
        except Exception:  # noqa: BLE001 - stub page without the API
            return None
    shim = f"(_root, a) => ({js})(a)"
    try:
        return page.locator(":root").evaluate_handle(shim, arg, timeout=QSA_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        try:
            page._ax_too_slow = True
        except Exception:  # noqa: BLE001
            pass
        return None
    except Exception:  # noqa: BLE001 - bad JS / detached; not a wedge, do not latch
        return None


def _bounded_page_eval(page: Any, js: str, arg: Any = None, *,
                       timeout_ms: int = QSA_TIMEOUT_MS,
                       default: Any = None) -> Any:
    """`page.evaluate(js, arg)`, BOUNDED — returns ``default`` on timeout.

    Bare `page.evaluate` takes NO timeout and `set_default_timeout` does NOT
    apply to it (see `browser_actions._bounded_eval`'s docstring). On the b0811
    `miniapp_interactive-art_generati__a2YwBBP` artifact (Scroll Canyon Garden,
    WebGPU/WebGL2), the renderer main thread is pegged continuously by shader
    work — measured 15+ min at zero progress inside a single unbounded
    `page.evaluate` — and every action-verb helper in the executor that reaches
    for `page.evaluate(js)` or `page.evaluate(js, arg)` wedges the whole
    trajectory. Greenlet dumps confirmed the wedge migrating from
    `install_fps_counter` → `dismiss_entry_gate._probe` → `_scroll_pos` as each
    site was bounded, so the fix must be at the class level (this helper) rather
    than per-site.

    Route through `page.locator(":root").evaluate(shim, arg, timeout=)` (the
    same in-greenlet idiom `_bounded_evaluate_handle` uses): `Locator.evaluate`
    HAS a `timeout=` and IS honoured against a pegged main thread. The shim
    `(_root, a) => (${js})(a)` drops the injected root element and forwards the
    original arg unchanged, so callers whose JS is written `fn()` or `fn(arg)`
    do not have to rewrite anything.

    On a stub page without `locator` (smoke tests), fall back to the raw
    `page.evaluate` so existing test doubles behave unchanged. On timeout the
    default is returned; the caller's code path is exactly the one it already
    takes when `page.evaluate` raises for a closed context or bad JS."""
    if not hasattr(page, "locator"):
        try:
            return page.evaluate(js, arg) if arg is not None else page.evaluate(js)
        except Exception:  # noqa: BLE001
            return default
    shim = f"(_root, a) => ({js})(a)"
    try:
        return page.locator(":root").evaluate(shim, arg, timeout=timeout_ms)
    except Exception:  # noqa: BLE001 — timeout / bad JS / detached
        return default


def _first_measurable(page: Any, selector: str) -> Any:
    """The first MEASURABLE match, resolving a comma union in authored order.

    A comma union is a priority list to the planner and an unordered set to CSS
    (DEFECT 44 above): the leading branch is its precise selector, the trailing
    branch a page-wide net, and CSS's document-order resolution silently picks
    whichever matches first in the DOM — the wrong control on any real panel. So
    each branch is tried ALONE, left to right, and the first that yields a
    measurable element wins. Only if none does is the whole union resolved, which
    keeps the previous behaviour as the floor.

    `xpath=a | b | c` is the same priority list written in the other dialect, and
    it reaches here on every coordinate verb — which is where DEFECT 63 was
    found. See `_xpath_union_branches`.
    """
    for branch in (_union_branches(selector)
                   or _xpath_union_branches(selector)):
        el = _first_measurable_one(page, branch)
        if el is not None:
            try:
                box = el.bounding_box()
            except Exception:  # noqa: BLE001 - detached
                box = None
            # Require a MEASURABLE hit to stop here. A branch that matches only a
            # hidden twin must not shadow a later branch that matches the real
            # control — that would trade this defect for the FGfZjZb one below.
            if box and box.get("width") and box.get("height"):
                return el
    return _first_measurable_one(page, selector)


def _first_measurable_one(page: Any, selector: str) -> Any:
    """The first match with a non-zero box, not merely the first match.

    Playwright's `query_selector` returns DOCUMENT ORDER, and a hidden twin
    frequently comes first. b0811 `FGfZjZb` renders into `<canvas id="gl">` but
    falls back to Canvas2D on a software rasteriser — `switchToFallback()` sets
    `gl.hidden = true` and un-hides `<canvas id="c2d">`, both inside `#stage`.
    So the target `canvas` resolved to `#gl`: present, first, and 0x0. A
    zero-area box is correctly treated as unaimable, the coordinates were
    dropped, and the drag ("stir the fluid current" — the artifact's single
    headline interaction, and a critical item) was lost while the real 1280x720
    canvas sat one sibling away. The trace even said so: "GPU TOO SLOW — CANVAS
    FALLBACK ENGAGED".

    Falling through to a later match is safe in a way that falling back to a
    broader selector is not: every candidate already satisfies the selector the
    planner wrote, so we are choosing among elements it asked for, not guessing a
    different one. Returns the first match when NONE is measurable, so the
    caller's existing unmeasurable path (drop the coordinates, degrade the item)
    is unchanged.

    Scans EVERY match, not a capped prefix — see DEFECT 73 at `_MEASURABLE_SCAN`.
    BxrjKKx's real slider sat at index 18 of 32 behind a slide deck's hidden
    views, and the cap is what lost it.
    """
    els = _bounded_query_all(page, selector)
    if not els:
        return None
    for el, ok in zip(els, _scan_flags(page, els, _BOX_FLAGS_JS, _BOX_ONE_JS)):
        if ok:
            return el
    return els[0]


# DEFECT 50: an ambiguous selector whose first match is an ALREADY-SELECTED TAB.
#
# `_pin_union` fixes a union the planner wrote. This is the same wrong-target
# click with no union to split: ONE selector that happens to match two things.
#
# b0811 `9eLur2v` scored 0.000 on the critical item "Admin login lands in
# console". The plan clicks `button:has-text("SIGN IN")`, and that page has two:
#
#   [0] <button type="button" role="tab" aria-selected="true" class="on">   y=189
#   [1] <button type="submit" class="btn btn-gold btn-block mt-6">          y=399
#
# Playwright resolves a multi-match in document order, so every run clicked the
# TAB. Measured on the extracted artifact: clicking [0] leaves the page on the
# auth form; clicking [1] reaches a full admin console ("ADMINISTRATOR CONSOLE",
# "Dashboard", "SIGN OUT", "AVG PROGRESS"). The judge's FALSE was a faithful
# reading of a trace produced by clicking the wrong button -- a false negative
# worth a whole artifact, since the item is critical and gates the score to zero.
#
# The discriminator is the ARIA tab pattern, not a class name or a y-coordinate:
# clicking a tab whose `aria-selected` is already `true` is a no-op BY DEFINITION
# of that pattern -- it re-selects what is selected. So excluding it can never
# lose a real interaction, which is the entire safety argument. `aria-pressed` is
# deliberately NOT included: a pressed toggle un-presses, which is a real effect.
#
# Expressed as a selector rewrite rather than an index so `_selector_present`,
# the AX note and the recorded step's `target` all keep naming what was aimed at
# -- the same reason `_pin_union` returns a string.
_ARIA_SELECTED_NOT = ':not([aria-selected="true"])'


def _pin_ambiguous(page: Any, selector: str) -> str:
    """Narrow a multi-match selector past an already-selected tab. Never raises.

    Returns the original selector unless ALL of: it is CSS (an XPath cannot take
    the `:not()` suffix), it matches more than one element, at least one match is
    an already-selected tab, and the narrowed form still matches something.
    """
    sel = (selector or "").strip()
    if not sel or sel.startswith("xpath=") or sel.startswith("//"):
        return selector
    if _ARIA_SELECTED_NOT in sel:
        return selector
    hits = _bounded_query_all(page, sel)
    if len(hits) < 2:
        return selector
    narrowed = f"{sel}{_ARIA_SELECTED_NOT}"
    kept = _bounded_query_all(page, narrowed)
    # Only act when the suffix actually DROPPED something (there was a selected
    # tab among the matches) and left something to click.
    if kept and len(kept) < len(hits):
        return narrowed
    return selector


# DEFECT 56: when the matches NEST, Playwright clicks the outermost one.
#
# Playwright resolves a multi-match in document order and, having warned
# `resolved to N elements. Proceeding with the first one`, clicks it. When the N
# matches are siblings that is a coin flip between plausible targets, and
# `_pin_union`/`_pin_ambiguous` above handle the cases where it goes wrong. When
# the N matches are NESTED -- an element and its own ancestors -- it is not a
# coin flip. Document order puts the outermost first every time, so the click
# lands on the container that merely CONTAINS the target.
#
# b0811 `BxrjKKx`, the critical item "Side menu navigation works":
#
#   div:has-text("06")  -> resolved to 27 elements
#                          first: <div id="menuList" class="menu__list">
#
# The plan wanted station row 06. It got the list that holds every row. The
# click lands on the container's centre -- some other row, or the gap between
# them -- and four separate attempts (steps 17, 29, 35, 41) each reported the
# menu items as `outside of the viewport`. The judge concluded the side menu
# never opened. The menu opened fine; we clicked the wrong box.
#
# `:has-text()` is the usual source because it matches on SUBTREE text, so every
# ancestor of a label matches by construction -- the same mechanism as defect 54,
# reaching the click verbs instead of the drag ones. Census over the 4,111 b0811
# trajectories: 1,815 steps in 939 tasks (22.8%) recorded `resolved to N
# elements` with N > 1; 1,148 of them carry a `:has-text()` clause, and 307
# demonstrably landed on a container (`<div>` 289, `<body>` 16, `<section>` 2).
#
# The remedy states the rule directly in CSS: among a set of matches, keep only
# those that do not CONTAIN another match. `sel:not(:has(sel))` -- an element
# that matches but has no matching descendant -- is exactly the innermost match,
# and Playwright accepts a `:has-text()` clause nested inside `:has()`. Measured:
#
#   div:has-text("06")                  2 matches, first DIV#menuList
#   div:has-text("06"):not(:has(...))   1 match,   DIV.menu__item  <- the row
#
# Safety: this only ever DROPS elements that contain another match, so a
# selector already resolving to one element, or to N disjoint siblings, is
# returned untouched -- verified in the smoke on a plain `button:has-text()`,
# an id, and a sibling set. When the rewrite would leave nothing (every match
# nests inside another, which `:has-text()` cannot produce but a hand-written
# selector could), the original is returned and behaviour is unchanged.
def _pin_nested(page: Any, selector: str) -> str:
    """Narrow a multi-match selector to its INNERMOST matches. Never raises.

    Returns the original selector unless ALL of: it is CSS (`:has()` cannot be
    appended to an XPath), it matches more than one element, and the rewrite
    both drops something and leaves something.
    """
    sel = (selector or "").strip()
    if not sel or sel.startswith("xpath=") or sel.startswith("//"):
        return selector
    # A union has to be pinned to one branch first (`_pin_union`); wrapping a
    # comma list in `:has()` would change what the branches mean.
    if _union_branches(sel):
        return selector
    if ":not(:has(" in sel:
        return selector
    hits = _bounded_query_all(page, sel)
    if len(hits) < 2:
        return selector
    narrowed = f"{sel}:not(:has({sel}))"
    kept = _bounded_query_all(page, narrowed)
    # Act only when the matches actually NESTED (the rewrite dropped something)
    # and an innermost match survives to be clicked.
    if kept and len(kept) < len(hits):
        return narrowed
    return selector


# DEFECT 68: the first match is INERT PROSE that merely NAMES the control.
#
# The third member of the wrong-target-click family, and the one the other two
# cannot reach. `_pin_ambiguous` needs an already-selected ARIA tab among the
# matches; `_pin_nested` needs the matches to nest. Here the matches are
# DISJOINT SIBLINGS and neither is a tab — one is a paragraph, one is a button.
#
# b0811 `9nZimj5` scored 0.000 on the critical item "Start tour highlights
# numbered elements". The plan clicks `text=Start tour`, and that page has six
# matches. Measured on the extracted artifact, after launching the assistant:
#
#   [0] <b>Start tour</b>            visible, inside the prose "click Launch
#                                    Assistant, then Start tour. The overlay
#                                    spotlights the numbered elements above"
#   [1] <button class="pal">         hidden (a palette entry)
#   [2..4] <option>                  hidden (a <select> of scene names)
#   [5] <button class="atx-qb">      visible  <- the widget's real chat chip,
#                                    wired to startTour() at avatar-widget.js:1018
#
# Playwright resolves a multi-match in document order, so the click landed on
# the `<b>` — a instructional sentence telling the user to click the button. The
# click did nothing (the AX tree recorded only a focus shift), and the judge
# reported the truth about the trace it was given: "no overlay, spotlight, or
# tooltip elements appearing". A faithful reading of a trace produced by
# clicking a word in a paragraph. Because the item is critical, that false
# negative cost the whole artifact: 0.811 -> 0.000.
#
# This shape is created by the artifacts themselves: a demo page that documents
# its own interaction ("click X, then Y") contains the label of every control it
# describes, in prose, ABOVE the control. `text=` and `:has-text()` match on
# rendered text, so the documentation always wins document order.
#
# The discriminator must be INTERACTIVITY, not position or tag, because the
# prose is not always first and the real control is not always a <button>:
# `[role=button]`, `<a href>`, `<summary>`, and a `tabindex` div are all real
# controls. So: among the matches, keep only those that are interactive. A `<b>`
# inside a `<p>` is not, and neither is a bare `<div>` of text.
#
# Safety rests on three restrictions:
#   1. it only ever DROPS matches, and only when at least one interactive match
#      survives — so a selector that matches only non-interactive elements
#      (clicking a plain div that has a JS handler, which does happen) is
#      returned untouched;
#   2. it is applied only when the FIRST match is non-interactive while a LATER
#      one is interactive. If the first match is already interactive, document
#      order is picking a control and this defect is not present;
#   3. `click`/`hover` only, like `_pin_ambiguous`. `type` needs the input it was
#      aimed at, and a drag's endpoints go through `_query_measurable`.
#
# Expressed as an `nth=` pin rather than a CSS rewrite, because `text=` is a
# Playwright engine selector, not CSS: no `:not()` suffix can be appended to it,
# and `text=Start tour` is precisely the shape this defect arrives in. `nth=` is
# the one narrowing that works across every engine.
#
# Restricted to TEXT-matching selectors, and that restriction is load-bearing in
# two directions. Positively: prose can only shadow a control when the selector
# matches on rendered text, so `text=`, `:has-text()` and `:text()` are the whole
# population — 18,460 click/hover steps on b0811, 3,838 of them (20.8%) recorded
# a multi-match, across 1,563 tasks (38.0%). Negatively: without it this pass
# pre-empts DEFECT 64. `canvas` matches the layered-canvas idiom's render layer
# `#gl` (no tabindex, so non-interactive here) and its input overlay `#ov`
# (`tabindex=0`), so a general interactivity pin would grab `#ov` by luck and
# skip the occlusion reasoning that arrived at it on purpose — same element, but
# by accident, with 64's diagnostics lost. Structural selectors keep their own
# narrowings.
_TEXT_SELECTOR_MARKS = ("text=", ":has-text(", ":text(")

# Interactive by ROLE, mirroring what a user can click. `contenteditable` and a
# non-negative `tabindex` are included because both make an arbitrary element
# focusable and clickable; `tabindex="-1"` is excluded — it is programmatic
# focus, not a control.
_INTERACTIVE_JS = """el => {
  const t = el.tagName;
  if (['BUTTON','A','INPUT','SELECT','TEXTAREA','SUMMARY','OPTION','LABEL'].includes(t)) {
    return t !== 'A' || el.hasAttribute('href');
  }
  const r = (el.getAttribute('role') || '').toLowerCase();
  if (['button','link','tab','checkbox','radio','switch','menuitem','option',
       'slider','spinbutton','combobox','textbox'].includes(r)) return true;
  if (el.isContentEditable) return true;
  const ti = el.getAttribute('tabindex');
  return ti !== null && !ti.trim().startsWith('-');
}"""

# `_INTERACTIVE_JS` applied to a whole match list in one round-trip. Defined by
# textual composition rather than duplicated so the two can never drift: there is
# exactly one copy of the predicate.
_INTERACTIVE_FLAGS_JS = (
    "els => { const one = (" + _INTERACTIVE_JS
    + "); return els.map(el => (el && one(el)) ? 1 : 0); }"
)


# DEFECT 74: `_pin_nested` narrows to the innermost match OF THE PLANNER'S TAG,
# which is not the innermost match on the page when the real control is a
# different tag.
#
# Defect 56 taught the pin to prefer `sel:not(:has(sel))`. That is correct as far
# as it goes, but `sel` carries the planner's tag guess, and the planner guesses
# `div` for anything it cannot see. When the actual control is a `<button>`, the
# `div` constraint hides it from BOTH sides of the rewrite: the button is not a
# candidate, and it does not disqualify the container that holds it. So the pin
# announces "[pinned to innermost match]" and hands back a container.
#
# This is the defect that MASKED defect 73. b0811 `BxrjKKx`, station navigation,
# measured on the extracted artifact after opening the side menu:
#
#   div:has-text("11")                       17 matches
#   div:has-text("11"):not(:has(div:...))     6 matches, first:
#       DIV.menu__list#menuList  396x534  209 descendants   <- the whole list
#   the control the plan meant:
#       BUTTON.mitem  381x54  "11Bacterial Growth Incubator"
#
# The click on a 396x534 container reported `ok` and navigated NOWHERE — the page
# stayed on the overview. Two steps later the plan dragged `input[type='range']`,
# all 32 of which are in un-navigated views, none measurable, and the drag failed
# `[drag target not measurable]`. That failure was read as defect 73 surviving its
# fix. It was not: with the navigation repaired the same page yields 32 matches
# `measurable at [18]`, which is precisely the index-past-the-cap case defect 73
# fixed. A false-`ok` upstream had made the downstream fix untestable.
#
# The remedy re-pins TAG-FREE and requires the survivor to be inside a control.
# Two conditions, both necessary:
#
#   1. `*:has-text(T):not(:has(*:has-text(T)))` — the innermost element on the
#      page carrying the text, whatever its tag. On BxrjKKx that is
#      `SPAN.thumb__n` (16x13), the station number inside the row.
#   2. the survivor must have an INTERACTIVE ANCESTOR (`el.closest`-style walk
#      using the one `_INTERACTIVE_JS` predicate). A click on a leaf inside a
#      button bubbles to the button, so aiming at the span drives the row; a leaf
#      inside inert prose drives nothing, and is skipped. Without this condition
#      the pin would trade a container click for a prose click — defect 68's
#      failure mode, arrived at from the other direction.
#
# Restriction, deliberately narrow: this runs ONLY when the current selector's
# first match still CONTAINS the text in a descendant, i.e. it is demonstrably a
# container rather than the labelled control itself. A `button:has-text("Run")`
# whose text is its own is left untouched, so the common case pays one extra
# descendant scan and nothing changes.
_TAG_TEXT_RE = re.compile(
    r"""^([A-Za-z][A-Za-z0-9]*):has-text\((["'])(.*?)\2\)"""
    r"""(?::not\(:has\(\1:has-text\(\2.*?\2\)\)\))?$""")

# Does any DESCENDANT still carry the text? If so the match is a container, not
# the labelled element. Cheaper and more honest than re-running the selector:
# `:has-text()` is a Playwright extension and is not valid inside
# `querySelectorAll`, so the DOM walk is the direct way to ask.
_CONTAINS_TEXT_JS = ("a => [...a.el.querySelectorAll('*')].some("
                     "n => (n.textContent || '').includes(a.t))")

# Visible AND inside a control. One page-side pass over the whole match list,
# composed from the single copy of the interactive predicate.
_IN_CONTROL_FLAGS_JS = (
    "els => { const one = (" + _INTERACTIVE_JS + "); return els.map(el => {"
    " if (!el) return 0;"
    " const r = el.getBoundingClientRect();"
    " if (!(r.width > 0 && r.height > 0)) return 0;"
    " for (let n = el; n; n = n.parentElement) { if (one(n)) return 1; }"
    " return 0; }); }")

# The same predicate for ONE element, for `_scan_flags`'s degraded path.
_IN_CONTROL_ONE_JS = (
    "el => { const one = (" + _INTERACTIVE_JS + ");"
    " const r = el.getBoundingClientRect();"
    " if (!(r.width > 0 && r.height > 0)) return false;"
    " for (let n = el; n; n = n.parentElement) { if (one(n)) return true; }"
    " return false; }")


def _pin_tagfree(page: Any, selector: str) -> str:
    """Re-pin a tag-constrained text selector tag-free, into a real control.

    Returns the original selector unless ALL of: it is a bare
    ``tag:has-text("T")`` (optionally already carrying defect 56's
    ``:not(:has(...))``), its first match is NOT itself a control, that match
    still contains the text in a DESCENDANT (so it is a container), and the
    tag-free innermost pin yields a visible match with an interactive ancestor.
    Never raises.
    """
    sel = (selector or "").strip()
    m = _TAG_TEXT_RE.match(sel)
    if not m:
        return selector
    quote, text = m.group(2), m.group(3)
    if not text.strip():
        return selector
    try:
        # `page.query_selector` is unbounded — route through the bounded scan and
        # take the first hit, so a pegged renderer degrades to "no match" rather
        # than wedging the whole action (same DEFECT-120 class as _bounded_query_all).
        _hits = _bounded_query_all(page, sel)
        first = _hits[0] if _hits else None
        if first is None:
            return selector
        # GUARD 1, and the one that keeps this pass off the common case. If the
        # current match is ITSELF a control, the planner's tag guess was right and
        # there is nothing to repair -- whatever its descendants contain.
        #
        # Measured on the 4,111 b0811 trajectories: 14,460 steps (11.7%) have the
        # bare `tag:has-text()` shape this pass can rewrite, and 13,494 of them
        # (93.3%) are `button:has-text()`. A button whose label sits in a <span>
        # DOES have a descendant carrying the text, so guard 2 alone would rewrite
        # nearly all of them. Each such rewrite is harmless in effect -- the click
        # on the span bubbles to the button -- but it is a page-wide tag-free
        # re-scan that could select a deeper match somewhere else entirely. Churn
        # on 13,494 working steps to fix 493 broken ones is the wrong trade, so
        # this guard reduces the pass's reach to `div`-and-friends: the tags the
        # planner guesses when it cannot see the control.
        if bool(first.evaluate(_INTERACTIVE_JS)):
            return selector
        # GUARD 2: does a DESCENDANT still carry the text? If not, this match is
        # the labelled element itself and there is no container to escape.
        if not page.evaluate(_CONTAINS_TEXT_JS, {"el": first, "t": text}):
            return selector
        free = f"*:has-text({quote}{text}{quote})"
        pinned = f"{free}:not(:has({free}))"
    except Exception:  # noqa: BLE001 - stub page, bad selector, detached handle
        return selector
    hits = _bounded_query_all(page, pinned)
    if not hits:
        return selector
    flags = _scan_flags(page, hits, _IN_CONTROL_FLAGS_JS, _IN_CONTROL_ONE_JS)
    for idx, ok in enumerate(flags):
        if ok:
            return pinned if idx == 0 and len(hits) == 1 else f"{pinned} >> nth={idx}"
    return selector



def _pin_prose(page: Any, selector: str) -> str:
    """Pin past a leading non-interactive match to the first interactive one.

    Returns the original selector unless ALL of: it matches on TEXT, it matches
    more than one element, the FIRST match is not interactive, and some later
    match is both interactive and visible. The result is
    `"<selector> >> nth=<i>"`. Never raises.
    """
    sel = (selector or "").strip()
    if not sel or ">> nth=" in sel:
        return selector
    if not any(m in sel for m in _TEXT_SELECTOR_MARKS):
        return selector
    hits = _bounded_query_all(page, sel)
    if len(hits) < 2:
        return selector
    # Both predicates over EVERY match, two round-trips total (DEFECT 73). The
    # capped prefix used to mean a text selector matching a long list could not
    # see a control past index 12 — the same blind spot, on the click path.
    flags = _scan_flags(page, hits, _INTERACTIVE_FLAGS_JS, _INTERACTIVE_JS)
    if not flags or flags[0]:
        # The first match is already a control: document order is right, and this
        # defect is not the one in play.
        return selector
    if not any(flags):
        # Nothing interactive anywhere — including the degraded stub path, where
        # every flag is False. Returning the selector untouched is restriction 1.
        return selector
    boxes = _scan_flags(page, hits, _BOX_FLAGS_JS, _BOX_ONE_JS)
    for idx, ok in enumerate(flags):
        if not ok:
            continue
        # Prefer an interactive match that is also VISIBLE. The hidden palette
        # button at [1] of `9nZimj5` is interactive but unclickable, and pinning
        # to it would trade a wrong-target click for a timeout.
        if idx < len(boxes) and boxes[idx]:
            return f"{sel} >> nth={idx}"
    return selector


def _xpath_union_branches(selector: str) -> list[str]:
    """Split an `xpath=` union on its top-level `|`, or [] if not splittable.

    DEFECT 63. `_union_branches` knows only the CSS union separator, so an XPath
    union is handed to Playwright whole and resolved in document order — the same
    DEFECT 44 the comma path was written to close, through a door nobody had
    checked. It matters more here, because of how these unions are written:

        xpath=//div[contains(text(), 'COOLING RATE')]/following-sibling::div//input[@type='range']
            | //input[preceding-sibling::*[contains(text(), 'COOLING')]]
            | //div[...][preceding-sibling::div[contains(text(), 'COOLING')]]
            | //input[5]

    The last branch is a POSITIONAL CATCH-ALL, and a positional branch always
    matches. So when the three named branches miss — as they did on b0811
    `xXbgiFY`, whose sliders are not `following-sibling` of their labels — the
    union still resolves, the step records `ok`, and the drag lands on whichever
    range input came first in the document. The judge then reported the truth:
    "a drag action was performed on the 'COOLING RATE' slider ... but the slider
    value remained stuck at 0.45". Two critical items flipped to False and the
    task went 1.000 -> 0.000 on a control that was never touched.

    A wrong-target drag that reports success is the worst outcome the executor
    has, and an XPath union is a more reliable way to produce one than a comma
    union, because a `//input[N]` tail cannot fail to match.

    Same conservatism as the comma splitter: bail out entirely on anything
    unbalanced rather than guess, and return [] for a single branch so callers
    keep the original string.
    """
    body = selector[len("xpath="):] if selector.startswith("xpath=") else None
    if body is None or "|" not in body:
        return []
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
            if depth < 0:
                return []
        elif ch == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if depth != 0 or quote:
        return []
    parts.append("".join(buf))
    out = [f"xpath={p.strip()}" for p in parts if p.strip()]
    return out if len(out) > 1 else []


def _pin_union(page: Any, selector: str) -> str:
    """Rewrite a comma union to the FIRST branch that matches something aimable.

    The same DEFECT 44 as `_first_measurable`, for the verbs that never touch it.
    `_action_click`/`_action_hover`/`_action_type` hand the selector straight to
    Playwright, which resolves a union in document order — so a plan that wrote
    `#reset, button:has-text("Reset")` and got a `PARAMS P` button back
    (b0811 `36LuAgk`) recorded `result: "ok"` on a wrong-target CLICK, the one
    failure mode the trace cannot show a reader.

    Returns a SELECTOR rather than an element handle because the callers below
    need a string: `_selector_present`, the AX-evidence note, and the recorded
    step's `target` all consume it, and pinning before those means the trace
    names the control that was actually aimed at.

    Conservative by construction — the original selector is returned whenever
    the union is not safely splittable (`:is(a, b)`, unbalanced quotes) or no
    single branch resolves to a measurable element, so behaviour only ever
    narrows onto a branch that would have worked on its own.
    """
    branches = _union_branches(selector) or _xpath_union_branches(selector)
    if not branches:
        return selector
    for branch in branches:
        el = _first_measurable_one(page, branch)
        if el is None:
            continue
        try:
            box = el.bounding_box()
        except Exception:  # noqa: BLE001 - detached between query and measure
            continue
        if box and box.get("width") and box.get("height"):
            return branch
    return selector


# DEFECT 46: consecutive clicks on the same canvas all land on the same pixel.
#
# `page.click("canvas")` aims at the element's CENTRE, every time, so a plan like
#
#   click canvas  "Click first point of polygon."
#   click canvas  "Click second point of polygon."
#   click canvas  "Click third point of polygon to close/create the body."
#
# places one vertex three times. Each step reports `result: "ok"` — the click WAS
# delivered — and no polygon is ever created. That is the b0811 `mKoh6BY` report
# ("The execution agent failed to create a polygon") plus its sibling item 3
# ("erase tool removes drawn bodies"), which cannot pass because there is nothing
# to erase. Both critical, both 0.000.
#
# Offsets from the centre, in fractions of the box so the spread scales with the
# live canvas: a triangle first (the common polygon probe), then a quad, then the
# vertical extremes. The first click of a run keeps the centre, so every
# single-click plan behaves exactly as before.
_CANVAS_CLICK_SPREAD = (
    (0.0, 0.0), (0.12, -0.10), (-0.12, -0.10), (0.12, 0.10), (-0.12, 0.10),
    (0.0, -0.16), (0.0, 0.16),
)


def _canvas_repeat_position(page: Any, selector: str,
                            n: int) -> dict[str, float] | None:
    """Pixel `position` for the n-th consecutive click on one canvas-like target.

    `n` counts from 0, and `n == 0` returns None — the first click of a run keeps
    Playwright's own centre-aiming. None also covers every case where the offset
    would be a guess: a target that is not canvas-like (a button clicked twice
    means twice on the button, not a scatter across it), or an element that cannot
    be measured. A None result is always "click as before", never a failure.

    Deciding this HERE, rather than in the planner, is deliberate: the planner has
    never touched the page, so all it could test is whether the selector string
    looks canvas-ish — which misses `#c`, `#sim`, and every other id an artifact
    happens to pick, while wrongly scattering clicks on a plain div called
    `#board`. The live tag is only knowable at execution time.

    `position` is measured from the element's TOP-LEFT while the spread is signed
    from the CENTRE, hence `w * (0.5 + fx)`. Clamped a couple of pixels inside the
    box because a position outside it makes Playwright raise, which would turn an
    aiming improvement into a lost interaction.
    """
    if n <= 0:
        return None
    el = _query_measurable(page, selector)
    if el is None or not _is_canvas_like(el):
        return None
    try:
        box = el.bounding_box()
    except Exception:  # noqa: BLE001 - detached between query and measure
        return None
    if not box or not box.get("width") or not box.get("height"):
        return None
    w, h = float(box["width"]), float(box["height"])
    fx, fy = _CANVAS_CLICK_SPREAD[n % len(_CANVAS_CLICK_SPREAD)]
    x = min(max(w * (0.5 + fx), 2.0), max(w - 2.0, 2.0))
    y = min(max(h * (0.5 + fy), 2.0), max(h - 2.0, 2.0))
    return {"x": round(x, 1), "y": round(y, 1)}


# DEFECT 52: two CSS dialect errors the planner makes constantly, each of which
# guarantees zero matches regardless of what the artifact contains.
#
# Census over the 4,111 b0811 baseline trajectories, counting a step as MISSED
# when its trace says "not measurable" / "selector not found" / bounding_box on
# None:
#
#     shape                    executed   missed   miss rate
#     :nth-of-type(N)              1317      635      48.2%
#     [aria-label*='word']         2324      918      39.5%
#     ---- neither shape ---      25948     3765      14.5%   (corpus baseline)
#
# 3.3x and 2.7x the baseline, over 3,641 planned steps in 967 tasks. Neither is
# a fact about the page; both are the planner speaking a dialect CSS does not:
#
#   * `[aria-label*='cooling']` -- attribute-substring matching is CASE-SENSITIVE
#     in CSS. The planner writes the label as prose ('cooling'); the artifact
#     wrote `aria-label="Cooling rate"`. Measured on `xXbgiFY`: the clause
#     matches 0 elements while the control it names sits right there. Selectors 4
#     has had the `i` flag for exactly this, and Chromium supports it.
#
#   * `:nth-of-type(5)` -- counts among SIBLINGS OF THE SAME TAG. The planner
#     means "the 5th slider on the page"; CSS reads "the 5th <input> inside its
#     own parent". `xXbgiFY` puts each slider in its own SECTION, so every one is
#     the 1st or 2nd input of its parent and N>=3 matches nothing anywhere.
#     Playwright's `:nth-match(sel, N)` is the thing the planner meant.
#
# Both rewrites landed on the control the step was actually about in 6/6 cases
# on `xXbgiFY` (verified against each target's aria-label, not merely "something
# matched"), and both are inert on selectors that already resolve -- checked
# against `input[type=range]`, `#sCool`, an exact-value attribute clause, a
# `:has-text` button, and `canvas`. See `_smoke_selector_dialect`.
#
# The `i` flag goes only on QUOTED attribute clauses. An unquoted value cannot
# hold the flag, and rewriting `[disabled]` or `[data-n=3]` would be a syntax
# error that turns a missing element into a raising selector.
_ATTR_QUOTED_RE = re.compile(r"""\[\s*([-\w]+)\s*([~|^$*]?=)\s*(["'])(.*?)\3\s*\]""")
_NTH_OF_TYPE_RE = re.compile(r"^(.*?):nth-of-type\(\s*(\d+)\s*\)$")


def _ci_attributes(selector: str) -> str:
    """Add the CSS case-insensitivity flag to every quoted attribute clause.

    Returns the selector unchanged when it has no quoted clause, or when one
    already carries a flag — re-flagging would produce `[a='b' i i]`, which does
    not parse.
    """
    if not selector:
        return selector

    def sub(m: "re.Match[str]") -> str:
        return f"[{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(3)} i]"

    return _ATTR_QUOTED_RE.sub(sub, selector)


def _nth_of_type_to_nth_match(selector: str) -> str:
    """`X:nth-of-type(N)` -> `:nth-match(X, N)` — Nth on the PAGE, not Nth sibling.

    Only for a selector ENDING in the pseudo-class with a non-empty prefix.
    `:nth-match` takes a full selector as its first argument, so a trailing
    combinator (`div:nth-of-type(2) > input`) cannot be rewritten this way and is
    left alone rather than mangled.
    """
    m = _NTH_OF_TYPE_RE.match((selector or "").strip())
    if not m:
        return selector
    head = m.group(1).strip()
    if not head or head.endswith((">", "+", "~")):
        return selector
    return f":nth-match({head}, {m.group(2)})"


def _dialect_salvage(page: Any, selector: str) -> Any:
    """Retry a non-matching selector through the two dialect rewrites. Never raises.

    Applied per UNION BRANCH, because the planner mixes shapes inside one string
    (`input[aria-label*='cooling'], input:nth-of-type(5)`) and a whole-string
    rewrite would leave whichever branch it did not understand.

    Returns the first measurable element found, or None — and None is the safe
    outcome: the caller falls through to the label salvage and then, failing
    that, to the coordinate refusal, which degrades the item to indeterminate
    rather than aiming at the wrong pixel.

    A rewrite that matches MORE THAN ONE element is discarded. Relaxing case is
    what makes this necessary: `[aria-label*='CO']` names one control on a page
    of Title Case labels, but `[aria-label*='CO' i]` also catches `Cooling`,
    `Coefficient` and `Viscosity`, and `_first_measurable` would hand back
    whichever came first in document order — a silent wrong-target drag, worse
    than the lost interaction this function is trying to repair. `:nth-match`
    needs no such protection (it yields at most one by construction) but is held
    to the same rule for free.
    """
    sel = (selector or "").strip()
    if not sel or sel.startswith("xpath=") or sel.startswith("//"):
        return None
    for branch in [b.strip() for b in sel.split(",") if b.strip()]:
        for rewrite in (_ci_attributes(branch),
                        _nth_of_type_to_nth_match(branch),
                        _nth_of_type_to_nth_match(_ci_attributes(branch))):
            if rewrite == branch:
                continue
            hits = _bounded_query_all(page, rewrite)
            if len(hits) != 1:
                continue
            el = _first_measurable(page, rewrite)
            if el is not None:
                return el
    return None


# DEFECT 54: `:has-text()` + a combinator lands ONE ROW OFF, and says `ok`.
#
# The user's report on b0811 `mKoh6BY`: "interacting with wrong target slider".
# The plan aimed at FREESTREAM U with the planner's usual panel shape --
#
#   div:has-text("FREESTREAM U") + div input[type="range"],
#   div:has-text("FREESTREAM U") input[type="range"]
#
# -- and the trace afterwards reads `slider Kinematic viscosity focused=True`.
#
# `:has-text()` matches an element whose SUBTREE contains the text, so on a panel
# page EVERY ancestor of the label matches: the label's own `DIV.field`, the
# `SECTION.block` holding all four sliders, the `ASIDE`, and `<body>`. Playwright
# resolves that to the OUTERMOST one, and the adjacent-sibling combinator then
# walks from there -- from the whole panel, not from the label's row. Measured on
# the extracted artifact, whose four sliders each sit alone in a `DIV.field`:
#
#   div:has-text("FREESTREAM U")   + div input[type="range"] -> 1: Kinematic viscosity
#   div:has-text("ANGLE OF ATTACK")+ div input[type="range"] -> 1: Tracer particle count
#   div:has-text("FREESTREAM U")     div input[type="range"] -> 4: all of them
#
# This is worse than defect 52, which at least matched NOTHING and degraded the
# item honestly. Here the selector resolves to exactly ONE element, the drag
# succeeds, the step records `ok`, and the judge is shown a screenshot of the
# wrong control moving -- a false negative that looks like evidence. Defect 44's
# union-splitting cannot help: `_first_measurable` stops at the first MEASURABLE
# branch, and branch 1 IS measurable, just wrong.
#
# Census over the 4,165 b0811 plans (30,526 targets): 250 targets in 78 tasks
# pair `:has-text(...)` with a `+`/`~`/`>` combinator, 171 more with a descendant
# space. Both shapes walk from the same wrong node.
#
# The remedy is not another rewrite but a REORDERING: when a selector has this
# shape AND is aiming at a slider, resolve it through `_text_salvage` FIRST.
# That path is row-anchored by construction -- `:has(> ctl):has-text("X") ctl`
# requires the text and the control in the SAME element, the unanchored form is
# accepted only when unambiguous, and `_LABEL_SALVAGE_JS` walks UP from each
# control and stops at the first ancestor that is a row rather than a panel. Any
# of those is a stronger claim about which control the label names than "walk a
# combinator from whichever ancestor came first". When it abstains we fall
# through to the original selector unchanged, so this can only ever replace a
# guess with an anchored answer.
_HAS_TEXT_COMBINATOR_RE = re.compile(
    r""":has-text\(\s*(["']).*?\1\s*\)\s*(?:[>+~]|\s)\s*\S""")
# Restricted to slider-ish targets: `_text_salvage` only ever returns a
# `_SALVAGE_CONTROLS` element, so on a button target it would do nine queries to
# reach the same None. Keeping the trigger narrow also keeps the behaviour
# statement narrow — this changes slider resolution and nothing else.
_SLIDERISH_RE = re.compile(r"type\s*=\s*[\"']?(range|number)|role\s*=\s*[\"']?slider",
                           re.I)


def _has_text_row_shape(selector: str) -> bool:
    """Does this selector walk from a `:has-text()` clause to a slider?

    True only for the defect-54 shape: a text clause followed by a combinator (or
    a descendant space) and, somewhere in the string, a range/number/slider
    target. A bare `button:has-text("RESET")` has no combinator and is unaffected.
    """
    sel = selector or ""
    if ":has-text(" not in sel:
        return False
    return bool(_HAS_TEXT_COMBINATOR_RE.search(sel)) and bool(_SLIDERISH_RE.search(sel))


def _query_measurable(page: Any, selector: str) -> Any:
    """`_first_measurable`, plus the id-tail salvage above. Never raises."""
    if _has_text_row_shape(selector):
        # DEFECT 54 above: the selector will very likely RESOLVE, and to the wrong
        # row. Prefer the row-anchored answer; fall through untouched when it has
        # none to give.
        anchored = _text_salvage(page, selector)
        if anchored is not None:
            return anchored
    el = _first_measurable(page, selector)
    if el is None:
        # `query_selector_all` may be absent (stub pages in the smokes) or the
        # selector may be a shape only `query_selector` handles; try it before
        # concluding there is no match. Bounded — a pegged renderer already
        # latched _ax_too_slow above, so this call is a no-op.
        _hits = _bounded_query_all(page, selector)
        el = _hits[0] if _hits else None
    if el is not None:
        return el
    # DEFECT 52: the planner wrote a CSS DIALECT that cannot match, before any
    # question of the page's contents arises. Cheap, exact, and tried ahead of
    # the label salvage below because a rewrite that resolves is a certainty
    # while a label walk is an inference.
    el = _dialect_salvage(page, selector)
    if el is not None:
        return el
    m = _ID_TAIL_RE.match(selector.strip())
    if m:
        salvaged = _first_measurable(page, m.group(1))
        if salvaged is not None:
            return salvaged
        # `canvas #gl` where #gl exists but is the hidden fallback twin (FGfZjZb
        # again): the id is unique so there is no second candidate, but the
        # LEADING compound is a bare tag we can re-run the measurable scan on.
        # Restricted to a single whitelisted tag — a prefix like `.panel > .row`
        # names a container, not the thing to interact with.
        head = selector.strip()[:m.start(1)].strip()
        if head.lower() in _AX_TAGS:
            return _first_measurable(page, head.lower())
        return None
    return _text_salvage(page, selector)


# The label inside a `:has-text("...")` clause of a selector that matched
# nothing. `_relaxed_text_selector` does the same job for CLICKS; this is the
# coordinate-action counterpart, and it must be stricter about what it accepts.
_ANY_TEXT_RE = re.compile(r""":has-text\(\s*(["'])(.*?)\1\s*\)""")

# DEFECT 49: the same label, written as XPath instead of CSS.
#
# `_text_salvage` recovers a control from the label the planner quoted, but it
# only ever looked inside `:has-text(...)`. b0811 `ap57fCz` lost SIX slider drags
# because the planner wrote its labels the other way:
#
#   xpath=//div[contains(., 'HEIGHT')]/following-sibling::div//input[@type='range']
#       | //div[text()='HEIGHT']/following-sibling::input
#       | //input[preceding-sibling::div[contains(., 'HEIGHT')]]
#
# Measured against that artifact: the whole selector matches 0 elements and so
# does every `|` branch alone -- the sibling axes are guesses about a DOM the
# planner never saw. But the page holds `<input id="sl_body_height"
# aria-label="Height">` and twelve more, every one with a clean accessible name,
# so the one true thing in the string is again the LABEL. Three items (one of
# them CRITICAL) went indeterminate for want of extracting it.
#
# XPath is rare in this corpus -- 84 of 114,636 planned steps (0.1%), across 15
# tasks -- but when the planner does reach for it, it fails: 53 of 74 executed
# xpath steps errored (71.6%), 50 of them with the bare `'from'` of a drag whose
# target could not be measured.
#
# Only literals compared against TEXT or against a naming attribute are labels.
# `[@type='range']` and `[@class='slider']` are structure: feeding "RANGE" to the
# reader-style matcher would be asking for a control whose row says "range",
# which is either nothing (wasted work) or the wrong control (a silent
# wrong-target drag -- the exact failure `_SALVAGE_CONTROLS` exists to prevent).
_XPATH_LABEL_RE = re.compile(
    r"""(?:contains\s*\(\s*(?:\.|text\(\)|normalize-space\s*\(\s*\.?\s*\)"""
    r"""|@(?:aria-label|title|placeholder|label|alt))\s*,\s*(["'])(.*?)\1\s*\)"""
    r"""|(?:\.|text\(\)|normalize-space\s*\(\s*\.?\s*\)"""
    r"""|@(?:aria-label|title|placeholder|label|alt))\s*=\s*(["'])(.*?)\3)""",
    re.IGNORECASE,
)


def _quoted_labels(selector: str) -> list[str]:
    """Every label a selector quotes, CSS `:has-text()` and XPath alike.

    Order is preserved and duplicates dropped: a three-branch union naming the
    same label three times should cost one salvage attempt, not three.
    """
    out: list[str] = []
    for _, txt in _ANY_TEXT_RE.findall(selector or ""):
        if txt not in out:
            out.append(txt)
    for _, a, _q, b in _XPATH_LABEL_RE.findall(selector or ""):
        txt = a or b
        if txt and txt not in out:
            out.append(txt)
    return out

# What a salvaged drag/scroll target is allowed to be: a real form control. The
# restriction is the entire safety argument. b0811 `S3vxNU3` is the counter-
# example — it has ZERO controls (its Wind/Water/Sun are read-only `role=status`
# bars), an earlier 4-clause union resolved to the readout `DIV.cell`, the drag
# changed nothing (`tWind.style` stayed `width: 19%`), and the item was still
# judged True because the artifact animates continuously so ANY before/after
# pair differs. A relaxed text match that accepts any element reproduces exactly
# that false pass; one that accepts only controls abstains, which is correct.
_SALVAGE_CONTROLS = ("input[type=range]", "[role=slider]", "input[type=number]")

# DEFECT 47: a label the planner read off the screen can be a DIFFERENT CHARACTER
# from the one in the DOM, and `:has-text()` compares codepoints.
#
# b0811 `xXbgiFY` lost two drags this way. The artifact writes `μ` and `κ` and
# CSS-uppercases the panel, so the AX tree carries GREEK CAPITAL MU (U+039C) and
# KAPPA (U+039A); the planner transcribed the visually identical LATIN M and K.
# Every branch of
#
#   div:has-text("VISCOSITY M") + div input[type="range"],
#   div:has-text("VISCOSITY M") ~ input[type="range"],
#   input[type="range"]:nth-of-type(4)
#
# therefore matches nothing, the step records `error: "'from'"` with "drag target
# not measurable", and the item goes indeterminate — one of the "selector not
# found" failures that make automatic verification unreliable. Greek letters are
# not incidental here: this corpus is physics simulations, where μ, κ, ρ, θ, ω, Δ
# and λ are the normal way to name a parameter.
#
# The fold maps each confusable to the ASCII the planner is likely to have typed,
# in BOTH directions (`_fold_confusables` normalises the needle; the page text is
# normalised by the same map in the JS below). Only used by the salvage path, so a
# selector that already matched is never rewritten.
_CONFUSABLES = {
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "Х": "X", "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
}


def _fold_confusables(text: str) -> str:
    """Map look-alike letters to the ASCII a planner would have transcribed.

    Applied AFTER upper-casing, never before: the DOM usually holds the AUTHORED
    lowercase (`Lava viscosity μ`) and the capital exists only in the rendering,
    so `μ` -> `Μ` (U+039C) is `toUpperCase`'s doing and the fold has to run on its
    output. Folding first would leave the lowercase mu untouched, upper-case it to
    a Greek capital, and miss exactly the case this fixes.
    """
    return "".join(_CONFUSABLES.get(ch, ch) for ch in text or "")


def _fold_label(text: str) -> str:
    """Normalise a label for reader-style comparison: upper, fold, collapse space."""
    return " ".join(_fold_confusables((text or "").upper()).split())


# Find a control by its label the way a READER would match it: fold confusables,
# collapse whitespace, case-insensitively, and accept a label that merely contains
# the quoted text (the planner writes `VISCOSITY M` for a cell reading
# `VISCOSITY Μ  1.4`). Runs in the page because it has to compare against live
# text; returns the control itself so the caller can measure it. Mirrors
# `_fold_label` exactly — upper-case first, then fold.
#
# Two guards keep the widening from drifting onto a control the label was not
# about, which would trade a lost interaction for a silent wrong-target one:
#
#   * an ancestor that holds MORE THAN ONE control is not a label row, it is a
#     panel. Without this, `body` contains every row's text, so searching for
#     EXTINCTION K happily returns the VISCOSITY slider.
#   * BODY/HTML are never label rows, so a control whose only textual claim comes
#     from a distant readout (the `S3vxNU3` shape: a `role=status` cell reading
#     `WIND Ω 19%` next to an unrelated slider) resolves to nothing.
#
# Nearest match wins, and a tie at the same depth abstains — two controls with an
# equal claim to one label means the label does not identify either.
_LABEL_SALVAGE_JS = """(args) => {
  const MAP = args.map, needle = args.want, sel = args.sels.join(',');
  const fold = s => (s || '').toUpperCase().replace(/./gu, c => MAP[c] || c)
                             .replace(/\\s+/g, ' ').trim();
  if (!needle) return null;
  const ctls = Array.from(document.querySelectorAll(sel));
  let best = null, bestDepth = 99, tied = false;
  for (const c of ctls) {
    let node = c;
    for (let up = 0; up < 4 && node; up++, node = node.parentElement) {
      const tag = node.tagName;
      if (tag === 'BODY' || tag === 'HTML') break;
      // A label row wraps its own control, not the whole panel.
      if (up > 0 && node.querySelectorAll(sel).length > 1) break;
      const hay = fold((node.getAttribute('aria-label') || '')
                       + ' ' + (node.textContent || ''));
      if (!hay.includes(needle)) continue;
      if (up < bestDepth) { best = c; bestDepth = up; tied = false; }
      else if (up === bestDepth && c !== best) { tied = true; }
      break;
    }
  }
  return tied ? null : best;
}"""


def _text_salvage(page: Any, selector: str) -> Any:
    """Find the control the planner MEANT, from the label it quoted.

    b0811 `vJJzx4N` lost 3 drags to 4-clause unions in which every clause is
    valid CSS and (for two of the three) none matches: `input[type="range"]
    [id*="flux"], input[type="range"][id*="B"], .slider:has-text("FLUX
    DENSITY"), input[type="range"]:nth-of-type(3)` against a page whose slider
    is `<input id="c-b">` inside `<label>Flux density B₀<output>…</output>`.
    Every guess about the id and the class is wrong; the only true thing in the
    string is the LABEL, which the planner read off the AX tree. Measured: the
    wrapper form recovers `#c-b`, `#c-theta` and `#c-volt` — all three.

    Case-insensitively, because the planner writes the label as the UI
    upper-cases it (`FLUX DENSITY`) while the DOM text is `Flux density`.

    The third form is the accessible-name route, and it exists because a slider
    is often labelled the way an icon-only button is: `<input type="range"
    aria-label="Gravity">` with no text node anywhere near it. The two wrapper
    forms search for a control INSIDE something containing the text, so neither
    can reach a control that carries its own label as an attribute. Restricted to
    `_SALVAGE_CONTROLS` like the others, so the whole safety argument above still
    holds — whatever this returns is a real form control, never a readout DIV.

    The last resort is `_LABEL_SALVAGE_JS`, which matches the label the way a
    READER would (DEFECT 47): folding Greek/Cyrillic look-alikes to ASCII, since
    `:has-text("VISCOSITY M")` cannot match a panel rendering GREEK CAPITAL MU no
    matter how the CSS selector is shaped. Same `_SALVAGE_CONTROLS` restriction,
    so it inherits the same safety argument.

    The labels come from `_quoted_labels`, so an XPath `contains(., 'HEIGHT')`
    reaches both passes exactly as a CSS `:has-text("HEIGHT")` does (DEFECT 49).
    """
    for txt in _quoted_labels(selector):
        txt = txt.strip()
        if len(txt) < 3:  # a one-glyph label matches half the page
            continue
        esc = txt.replace("\\", "\\\\").replace('"', '\\"')
        for ctl in _SALVAGE_CONTROLS:
            for form in (f':has(> {ctl}):has-text("{esc}") {ctl}',
                         f':has-text("{esc}") {ctl}',
                         f'{ctl}[aria-label="{esc}"]'):
                # `:has-text(...) ctl` is UNANCHORED — it matches from `body`,
                # whose text contains every row of the panel, so on a multi-slider
                # page it returns the panel's FIRST control for any label whatever.
                # Measured on the xXbgiFY panel shape: asking for `ASH ρ` yields
                # `[visc, rho]`, and taking the first is a silent wrong-target
                # drag. Accept this form only when it is unambiguous; a tie is
                # left to the reader-style pass below, which resolves by nearest
                # ancestor.
                if form.startswith(":has-text"):
                    hits = _bounded_query_all(page, form)
                    if len(hits) != 1:
                        continue
                el = _first_measurable(page, form)
                if el is not None:
                    return el
    # Nothing matched as written. Retry every quoted label through the reader's
    # comparison before giving up.
    for txt in _quoted_labels(selector):
        txt = txt.strip()
        if len(txt) < 3:
            continue
        el = _bounded_evaluate_handle(
            page, _LABEL_SALVAGE_JS,
            {"map": _CONFUSABLES, "want": _fold_label(txt),
             "sels": list(_SALVAGE_CONTROLS)})
        el = el.as_element() if el is not None else None
        if el is None:
            continue
        try:
            box = el.bounding_box()
        except Exception:  # noqa: BLE001 - detached between query and measure
            continue
        if box and box.get("width") and box.get("height"):
            return el
    return None


def _viewport_of(page: Any) -> tuple[int, int]:
    """The live viewport, falling back to Playwright's default 1280x720.

    `run.py` builds the driving context with no explicit viewport, so the
    fallback is the real value in the sweep; reading it live keeps this correct
    if that ever changes (and for the mobile shot, which resizes the page).
    """
    try:
        vp = page.viewport_size or {}
        w, h = int(vp.get("width") or 0), int(vp.get("height") or 0)
        if w > 0 and h > 0:
            return w, h
    except Exception:  # noqa: BLE001 - stub page in the smokes
        pass
    return DESKTOP_VP


def _pt_in(pt: Any, w: int, h: int) -> bool:
    """True if `pt` is a usable [x, y] pair inside a w x h viewport."""
    if not isinstance(pt, (list, tuple)) or len(pt) < 2:
        return False
    x, y = pt[0], pt[1]
    return (isinstance(x, (int, float)) and isinstance(y, (int, float))
            and 0 <= x < w and 0 <= y < h)


# Is the PRESS point of a drag actually on the element we resolved? Returns the
# topmost element's identity so the refusal can be recorded, or None when the
# point does reach the target (itself, an ancestor, or a descendant of it —
# `<input type=range>` renders its thumb as a shadow child, and a wrapper
# `<label>` legitimately forwards the press).
_PRESS_HIT_JS = """([el, x, y]) => {
  const t = document.elementFromPoint(x, y);
  if (!t) return 'nothing';
  if (t === el || el.contains(t) || t.contains(el)) return null;
  return t.id ? '#' + t.id : t.tagName + '.' + String(t.className || '').slice(0, 24);
}"""

# Which targets the press-point hit-test is allowed to refuse. A real form
# control is driven by a pointer sequence CAPTURED on the element, so if the
# mousedown lands on an overlay the drag cannot possibly move it. Anything else
# — above all a canvas — is driven by a listener that is usually bound to
# `window` or `document`, which receives the event no matter what is painted on
# top: measured on a full-viewport `z-index:20` overlay, a window-bound
# `pointerdown` handler still fires, and FGfZjZb's canvas (topped by its own
# fixed `#scroll-track`) still repaints under the drag. Refusing those would
# discard the headline interaction of every canvas artifact in the set.
_DRAG_HIT_TAGS = frozenset({"input", "select", "textarea"})

# DEFECT 45: a canvas is not a slider, and dragging it like one draws across the
# UI instead of on the artwork.
#
# The drag geometry below was designed for a range input: press at 10% of the
# width, release at the far edge, sweep the full track. On a FULL-VIEWPORT canvas
# — the shape of every paint/simulation artifact in this corpus — that is a
# 1280px sweep starting at x=128, and the left 10% is where these artifacts put
# their control panel. The canvas element still extends underneath it (it is
# `position:fixed; inset:0`), so `_press_reaches` deliberately allows the press
# (see _DRAG_HIT_TAGS above: canvas listeners are window-bound and do fire), the
# step records `result: "ok"`, and the user sees the stroke land on the panel
# text. Both reports say exactly that:
#
#   59wRCLX: "The drag action dragged on the left panel texts (Suiboku ...)
#             instead of on the canvas."
#   xXbgiFY: "The old bug again. The drag action is performed on the texts
#             instead on the volcano in the canvas."
#
# A slider needs the widest possible sweep because the sweep IS the value. A
# canvas needs the opposite: a short stroke near the CENTRE, which is where the
# subject is (the volcano, the paper) and is the one region a side panel never
# covers. So drag a canvas as a gesture — a quarter-width diagonal through the
# middle — and keep the full-track sweep for the controls it was written for.
#
# Diagonal, not horizontal: a horizontal stroke through the exact vertical centre
# is the one path an orbit control can interpret as pure azimuth with zero
# elevation change, and several of these renderers clamp azimuth-only motion.
_CANVAS_DRAG_TAGS = frozenset({"canvas", "svg"})
_CANVAS_DRAG_FRAC = 0.25


def _is_canvas_like(el: Any) -> bool:
    """True for a canvas/svg, or a container that exists only to hold one.

    The planner's target is frequently the WRAPPER (`#stage` on `xXbgiFY`), not
    the canvas itself, and the wrapper is a plain div — so a tag test alone
    misses the case the defect was reported on. A div whose only element child is
    a canvas is that canvas for aiming purposes.
    """
    try:
        return bool(el.evaluate("""e => {
          const t = e.tagName.toLowerCase();
          if (t === 'canvas' || t === 'svg') return true;
          const kids = e.children;
          if (!kids || kids.length !== 1) return false;
          const k = kids[0].tagName.toLowerCase();
          return k === 'canvas' || k === 'svg';
        }"""))
    except Exception:  # noqa: BLE001 - detached, or a stub page in the smokes
        return False


def _press_reaches(page: Any, el: Any, frm: Any) -> str | None:
    """The occlusion check the drag path never had. None = reachable.

    A click gets Playwright's actionability gate and errors honestly with
    "intercepts pointer events"; `page.mouse.move/down/up` gets nothing. It
    dispatches at raw pixels whatever is painted there, so an occluded drag
    produces no error and a perfectly plausible before/after pair — the judge
    then reads a real state change (from the overlay's own animation) as the
    artifact responding. This is the same failure class as the force-click fake
    success, in the one code path with no defence against it.

    Measured on b0811 `dLmjAh5`: `#sBloom` resolves at [969,577,274,20], the
    press point [996,587] is topped by `DIV.gate-inner` and the release point by
    `#gate`, and the value stays 0.62; with `#gate` removed the identical drag
    drives it 0.62 -> 1.6. The artifact ships a start gate the plan never
    dismissed — an artifact defect, and the honest outcome is an indeterminate
    item, not a fabricated determinate one.

    Only the PRESS point is tested, deliberately. `mousedown` on a control
    captures the pointer, so the rest of the sweep reaches it even across an
    overlay: measured, press-on-visible/release-under-cover drives a range input
    50 -> 100, while press-under-cover leaves it at 50. Testing `to` as well
    would refuse every working drag whose sweep passes beneath a fixed HUD.
    """
    try:
        tag = (el.evaluate("e => e.tagName") or "").lower()
    except Exception:  # noqa: BLE001 - detached, or a stub page in the smokes
        return None
    if tag not in _DRAG_HIT_TAGS:
        try:
            if not el.evaluate("e => e.getAttribute('role') === 'slider'"):
                return None
        except Exception:  # noqa: BLE001
            return None
    try:
        return page.evaluate(_PRESS_HIT_JS, [el, frm[0], frm[1]])
    except Exception:  # noqa: BLE001 - stub page without evaluate
        return None


# The disclosure that owns an element, expressed as the ARIA contract rather
# than a guess: a control that names this element's id in `aria-controls` and
# advertises `aria-expanded="false"`. Walk up from the target, because the
# collapsed container is usually an ancestor panel, not the control itself.
#
# The budget for clicking that owner is deliberately its own constant, and it is
# larger than it looks like it needs to be. This click is not on the critical
# path of a step — it is a rescue attempt for an element the viewport guard is
# about to refuse — so its downside is latency on already-doomed steps, while
# its upside is every control inside a collapsed panel. Measured on `Dfxc2vB`
# under load: a direct click on that same `#rack-tab` needed more than 2500ms,
# so a 1500ms internal budget silently gave up and 6 drags were lost where an
# unloaded run lost 3. A rescue path with a tighter deadline than the thing it
# is rescuing is a rescue path that stops working exactly when it is needed.
DISCLOSURE_CLICK_TIMEOUT_MS = int(
    os.environ.get("IV_DISCLOSURE_CLICK_TIMEOUT_MS", "5000"))

_OWNING_DISCLOSURE_JS = """(el) => {
  for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
    if (!n.id) continue;
    const sel = '[aria-controls="' + n.id + '"][aria-expanded="false"]';
    if (document.querySelector(sel)) return sel;
  }
  return null;
}"""


def _reveal_offscreen(page: Any, el: Any, box: dict[str, Any],
                      vw: int, vh: int) -> dict[str, Any]:
    """Open the collapsed panel a target is parked in, then re-measure it.

    `scroll_into_view_if_needed` handles "below the fold". It cannot handle the
    other way an element is out of reach: a side console translated off-screen by
    CSS while still being visible, enabled, and measurable. b0811 `Dfxc2vB` ships
    29 correctly-labelled sliders inside `#rack.collapsed { transform:
    translateX(var(--rack-w)) }`, so every one measures at x=1312 on a 1280-wide
    viewport. The viewport guard is right to refuse a drag there — a drag at
    x=1312 lands nowhere — but the result was 3 lost drags and a critical item
    ("Meteor rate slider decreases spawn frequency") that no artifact of this
    shape could ever satisfy. Measured: clicking the owning `#rack-tab` moves the
    slider to x=964 and the drag then drives it 18 -> 100.

    Deliberately narrow. It fires only when the target's CENTER is outside the
    viewport (an element merely overflowing its right edge is left alone), and it
    only follows an explicit `aria-controls` + `aria-expanded="false"` contract —
    clicking a guessed "probably a toggle" element would be an unlogged state
    change on the artifact under test. On any failure the original box stands, so
    the worst case is the unchanged pre-fix behaviour: coordinates dropped, step
    errors, item degrades to indeterminate.
    """
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    if _pt_in([cx, cy], vw, vh):
        return box
    try:
        sel = el.evaluate(_OWNING_DISCLOSURE_JS)
        if not sel:
            return box
        page.click(sel, timeout=DISCLOSURE_CLICK_TIMEOUT_MS)
        page.wait_for_timeout(600)  # panels slide in
        new = el.bounding_box()
    except Exception:  # noqa: BLE001 - stub page, detached node, unclickable tab
        return box
    if not new or not new.get("width") or not new.get("height"):
        return box
    ncx, ncy = new["x"] + new["width"] / 2, new["y"] + new["height"] / 2
    return new if _pt_in([ncx, ncy], vw, vh) else box


def _drop_coords(action: dict[str, Any], why: str) -> dict[str, Any]:
    """Strip a step's coordinates and record WHY, in the step's own note.

    Every caller here has decided that dispatching would aim at the wrong pixel,
    and the remedy is always the same: let the handler raise so the item degrades
    to indeterminate rather than taking a false determinate verdict (v0.6's
    DEGRADE-NEVER-GATE). What differed was the diagnosis, and two of the three
    sites recorded none — so the step surfaced as a bare `KeyError: 'from'`, which
    is the handler complaining about a key we deliberately withheld. Three gate
    rounds read that symptom without being able to distinguish "the canvas could
    not be measured" from "the press point is covered", which are opposite
    problems with opposite fixes.

    All four coordinate keys are stripped regardless of verb: `mouse_move` reads
    `x`/`y` where drag reads `from`/`to`, and a refusal that left `x` in place
    would be no refusal at all — the handler would find its argument and dispatch
    the very aim this function was called to prevent.
    """
    out = {k: v for k, v in action.items() if k not in ("from", "to", "x", "y")}
    out["note"] = ((out.get("note") or "") + f" [{why}]").strip()
    return out


def _resolve_coord_action(page: Any, action: dict[str, Any],
                          budget_ms: int | None = None) -> dict[str, Any]:
    """Give a target-only ``drag``/``scroll``/``mouse_move`` its required coordinates.

    ``browser_actions._action_drag`` reads ``action["from"]``/``["to"]``,
    ``_action_scroll`` reads ``["dx"]``/``["dy"]`` and ``_action_mouse_move`` reads
    ``["x"]``/``["y"]`` — raw subscripts, so a step
    that names a ``target`` but no coordinates raises ``KeyError: 'from'`` and the
    interaction is lost (23 steps on the b0810 pilot; the checklist author writes
    ``{"action":"drag","target":"input[type=range]"}`` because that is how a human
    would describe "drag the slider"). Fill the coordinates from the target's live
    bounding box: drag sweeps left-to-right across its middle — which is exactly
    how a range input is moved — scroll wheels down over its center, and a
    mouse_move goes to that center.

    ``mouse_move`` was added to this list late. Its handler has the same raw
    subscript and the same failure, and `planner.retarget_blind_probes` had begun
    REWRITING out-of-viewport mouse_move coordinates into a target — turning a
    step that was merely aimed badly into one that could not be aimed at all. 31
    of 400 b0811 tasks lost a probe that way, and because the resolver never
    covered the verb the whole class surfaced as a bare ``KeyError: 'x'``. A
    fix-time rewrite must land on a verb the executor can actually resolve.

    Coordinates the model supplied are honored ONLY if they fall inside the live
    viewport. The plan model is not told the page is driven at 1280x720 and
    invents coordinates for a taller one: 213 of 1,352 coordinate steps on b0811
    pointed outside it (y up to 1040), and a drag at y=815 does not miss — it
    lands on the full-screen canvas beneath, producing a confident AX diff about
    the wrong widget. `planner.retarget_blind_probes` fixes most of these at plan
    time; this is the executor-side backstop for a step that reaches here anyway,
    including one whose target is measured but whose element is scrolled out of
    view. When the coordinates are unusable and a target exists we measure the
    element instead; with neither, the handler's KeyError stands, which is the
    honest outcome (an unaimable interaction is an absence of evidence, and by
    contract that degrades the item to indeterminate rather than gating it).

    Returns the action unchanged when it already carries usable coordinates, when
    there is no target, or when the box cannot be measured.
    """
    verb = action.get("action")
    if budget_ms is None:
        budget_ms = ACTION_TIMEOUT_MS
    vw, vh = _viewport_of(page)
    if verb == "drag" and action.get("from") and action.get("to"):
        if _pt_in(action["from"], vw, vh) and _pt_in(action["to"], vw, vh):
            return action
        if not action.get("target"):
            return action  # nothing to fall back to; handler decides
        # else: fall through and re-derive from the target's live box
    if verb == "mouse_move" and action.get("x") is not None and action.get("y") is not None:
        if _pt_in([action["x"], action["y"]], vw, vh):
            return action
        if not action.get("target"):
            return action
        # else: fall through and re-measure, same as drag above
    if verb == "scroll" and ("dx" in action or "dy" in action):
        # `ms` is a common mis-spelling of the scroll delta in authored plans
        # ({"action":"scroll","target":"#track","ms":500}); dx/dy still win.
        return {"dx": action.get("dx", 0), "dy": action.get("dy", 0), **action}
    target = action.get("target")
    if not target:
        if verb == "scroll":
            raw = action.get("ms")
            dy = int(raw) if isinstance(raw, (int, float)) else 400
            return {**action, "dx": 0, "dy": dy}
        if verb == "mouse_move":
            # No target and no usable coordinates: aim at the viewport center.
            # A mouse_move is a HOVER-like probe over whatever fills the page --
            # on the canvas games in this corpus that is the canvas -- so the
            # center is the honest default, and unlike a drag it cannot commit a
            # destructive click on the wrong element. 25 probes in 400 b0811
            # tasks arrived with neither, and every one was lost to KeyError.
            return {**action, "x": vw // 2, "y": vh // 2,
                    "note": ((action.get("note") or "")
                             + " [no coords/target: viewport center]").strip()}
        return action
    scroll_err = None
    try:
        el = _query_measurable(page, normalize_selector(target))
        # An element below the fold is measured in PAGE coordinates, so its box
        # can sit outside the viewport — dragging there hits whatever is painted
        # at that pixel instead. Bring it into view first; the box is then
        # re-measured against the scrolled position.
        #
        # This wait is a FRAME COUNT, not a duration, and for a long time it was
        # the last place in the file that pretended otherwise. Playwright's
        # scroll settles on a stability check — the box unchanged across
        # CONSECUTIVE animation frames — so a hardcoded 1500ms buys ~90 frames at
        # 60fps and ~2.5 at 1.67fps, which is not enough to see two of them.
        # `vJJzx4N` proved it by bracketing itself: with a 2500ms action budget
        # (>=8fps) its ORIENTATION and ELECTRODE sliders scrolled into view and
        # both items scored; one round later at the 12000ms ceiling (<=1.67fps)
        # the identical pinned steps measured y=784 and y=989 — the stale
        # below-the-fold box, i.e. the scroll never happened — the drags were
        # refused, and the task fell 0.921 -> 0.000. Deleting v0.9's stall
        # threshold did not break this; it revealed that the pre-action wait was
        # never scaled the way the action it precedes had been since v0.8. A
        # rescue path with a tighter deadline than the thing it rescues stops
        # working exactly when it is needed, which is the same sentence already
        # written above DISCLOSURE_CLICK_TIMEOUT_MS for the same reason.
        #
        # And the failure must be RECORDED. Swallowing it left a stale box that
        # flowed on to the viewport guard, so the step reported "drag path leaves
        # the viewport" — which reads as a fixed overlay and sent the next round
        # looking at the wrong mechanism entirely.
        try:
            el.scroll_into_view_if_needed(timeout=budget_ms)
        except Exception as exc:  # noqa: BLE001 - detached, or a smoke stub page
            scroll_err = f"{type(exc).__name__}"
        box = el.bounding_box()
    except Exception as exc:  # noqa: BLE001 - no match, stub page, detached node
        el, box, why = None, None, f"{type(exc).__name__}: {exc}"[:80]
    else:
        why = "element has no box" if not box else "box has zero width/height"
    if not box or not box.get("width") or not box.get("height"):
        # Say WHY. Without this the step surfaces as a bare `KeyError: 'from'`,
        # which is the handler complaining about a key we declined to supply and
        # says nothing about the decision. `FGfZjZb`'s canvas drag failed this way
        # in fix2 and fix10 and succeeded in fix9, on a pinned plan — a real
        # nondeterminism that took three rounds to even be visible as one, because
        # every round reported the same downstream symptom.
        return _drop_coords(action, f"{verb} target not measurable ({why})")
    # Measurable but parked off-screen by a collapsed disclosure: open it.
    box = _reveal_offscreen(page, el, box, vw, vh)
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    if verb == "mouse_move":
        # Hover the element's center. Unlike a drag this is refused rather than
        # aimed blindly when the center is out of reach: moving the mouse to a
        # pixel that belongs to a different element produces a real AX/pixel diff
        # about the WRONG widget, which the judge then scores as a determinate
        # verdict on the item -- the same false-determinate this whole function
        # exists to prevent. Withholding the coordinates lets the handler raise,
        # and the item degrades to indeterminate.
        if not _pt_in([cx, cy], vw, vh):
            return _drop_coords(
                action, f"mouse_move target center ({cx:.0f},{cy:.0f}) is outside "
                        f"the {vw}x{vh} viewport")
        return {**action, "x": int(cx), "y": int(cy)}
    if verb == "drag":
        # Sweep across ~80% of the width. Left-to-right drives a range input
        # toward its MAXIMUM — a large, obvious change for the before/after
        # judge, which is the point of the step — and that is the right default
        # for the great majority of probes.
        #
        # But it was, for a long time, the ONLY thing this could do, and that
        # made a whole class of checklist item unfalsifiable. b0811 `Dfxc2vB`
        # authored two critical items on one slider — "rate can be raised to
        # 100" and "rate can be brought to 0" — and both probes swept to max.
        # Item 2 was judged False on an accurate observation ("the slider value
        # remained at 92 st/s"), gating a working artifact from 0.903 to 0.000;
        # `vJJzx4N` lost 0.808 the same way on "negative velocity reverses
        # flow". No artifact could have passed those checks, so they measured
        # the harness rather than the code.
        #
        # `planner.stamp_drag_directions` now infers the intent from the step's
        # own note (or its item's text) and stamps `direction`; a decrease
        # sweeps right-to-left. Pressing down at the far edge first snaps a
        # range input to that end, so the drag ends at the opposite extreme —
        # which is exactly the "reaches 0" the item is asking about.
        #
        # The two ends are NOT padded the same way, and that asymmetry is load-
        # bearing. The press point keeps its 10% inset so it lands on the
        # element rather than its border or a neighbour. The release point goes
        # to the true edge: with 10% on both sides a 0..100 slider stops at 8,
        # and an item that says "to 0" is judged False on a value of 8 — the
        # same false negative in a smaller disguise. Chromium clamps a drag past
        # the track end, so aiming at the edge is safe and lands exactly on the
        # extreme (verified against a real range input in
        # `_smoke_selector_dialects._directional_drag_moves_a_real_slider`).
        pad = box["width"] * 0.1
        edge = 1.0
        if _is_canvas_like(el):
            # DEFECT 45 (see _CANVAS_DRAG_FRAC): a short diagonal gesture through
            # the centre, not a full-width sweep whose start pixel is under the
            # side panel. `direction` still flips it, so a "decrease"/"reverse"
            # probe strokes the other way.
            span_x = box["width"] * _CANVAS_DRAG_FRAC / 2
            span_y = box["height"] * _CANVAS_DRAG_FRAC / 2
            sign = -1.0 if action.get("direction") == "decrease" else 1.0
            frm = [cx - sign * span_x, cy - sign * span_y]
            to = [cx + sign * span_x, cy + sign * span_y]
        elif action.get("direction") == "decrease":
            frm = [box["x"] + box["width"] - pad, cy]
            to = [box["x"] + edge, cy]
        else:
            frm = [box["x"] + pad, cy]
            to = [box["x"] + box["width"] - edge, cy]
        if not (_pt_in(frm, vw, vh) and _pt_in(to, vw, vh)):
            # Still unreachable after scrolling (fixed overlay, off-screen
            # panel). Dispatching would hit the wrong element; drop the
            # coordinates so the handler raises and the item degrades to
            # indeterminate rather than taking a false determinate verdict.
            #
            # Name the failed scroll when there was one. "Leaves the viewport"
            # alone describes a page that CANNOT bring the element into reach;
            # a timed-out scroll means we simply did not wait long enough, which
            # is our defect and not the artifact's. `vJJzx4N` reported the first
            # while suffering the second, and the two have opposite fixes.
            _sc = f", scroll_into_view failed: {scroll_err}" if scroll_err else ""
            return _drop_coords(
                action, f"drag path leaves the {vw}x{vh} viewport "
                        f"({frm[0]:.0f},{frm[1]:.0f} -> {to[0]:.0f},{to[1]:.0f})"
                        f"{_sc}")
        blocker = _press_reaches(page, el, frm)
        if blocker:
            # Inside the viewport but painted over: same remedy, and the reason
            # is recorded so the trace says "occluded" rather than showing a
            # bare unexplained failure.
            return _drop_coords(
                action, f"drag not dispatched: press point covered by {blocker}")
        return {**action, "from": frm, "to": to}
    # scroll: wheel over the element's center so an inner scroll container
    # receives the event rather than the document.
    raw = action.get("ms")
    dy = int(raw) if isinstance(raw, (int, float)) else 400
    try:
        page.mouse.move(cx, cy)
    except Exception:
        pass
    return {**action, "dx": 0, "dy": dy}


# --- Scroll: measure it, and don't trust the wheel ---------------------------
#
# `browser_actions._action_scroll` is `page.mouse.wheel(dx, dy)` followed by an
# unconditional `return {"result": "ok"}`. It captures NO evidence that the page
# moved, so the step record cannot distinguish "the wheel did nothing" from "it
# scrolled and the app ignored it". Two b0811 tasks were gated to 0.000 on
# exactly that ambiguity, and in both cases the judge's rationale was an accurate
# description of a harness failure:
#
#   `S3vxNU3` item 1: "the status text in the bottom-right corner remained stuck
#   at 'Station 0 — surface'". Measured here: `window.scrollY` stayed 0 after
#   `mouse.wheel(0, 900)`. Driven with `window.scrollBy` instead, the same page
#   advances to "Station 1 — aeolian transport" — the artifact was correct and
#   the check was measuring our wheel.
#
#   `FGfZjZb` item 1: "the chapter card heading ... remained 'Dusk on the water'".
#   Its scroller is `#scroll-track {position:fixed; inset:0; overflow-y:scroll}`,
#   so `window.scrollY` is permanently 0 and only an ELEMENT scrollTop moves.
#   Any scroll assertion that reads the document position is blind to it.
#
# Root cause of the swallowed wheel, isolated by bisection: the FIRST
# `mouse.wheel` after a `page.evaluate` lands nowhere on some pages
# (`[0, 1800, 2700]` across four 900px wheels, vs `[900, 1800, 2700]` with no
# preceding evaluate; reproducible 2/2 across settle times of 0-8s and unaffected
# by an intervening `mouse.move`). The verifier ALWAYS evaluates immediately
# before acting — `observe()` takes the AX tree and DOM snapshot for the BEFORE
# frame — so this hits the first scroll of essentially every before/after probe.
#
# Hence: measure both the document AND element scroll positions around the wheel,
# fall back to a scripted scroll when the wheel produced no movement, and record
# what happened. A scroll that still cannot move the page is reported as an
# error, so its item degrades to indeterminate rather than gating (spec:
# DEGRADE-NEVER-GATE).
_SCROLL_POS_JS = """() => {
  let inner = 0;
  document.querySelectorAll('*').forEach(e => { inner += e.scrollTop; });
  return [Math.round(window.scrollY), Math.round(window.scrollX), Math.round(inner)];
}"""

# DEFECT 57: a 3D viewer CONSUMES the wheel, so "position unchanged" is a lie.
#
# The reviewer's `xXbgiFY` remark asked for camera observation. Its critical item
# 3, "Distance attenuation affects ash", plans a single `scroll` to pull the
# camera in and out. The step recorded:
#
#   scroll had no effect (dy=-300, pos unchanged at [0, 0, 0], tried fallback:...)
#
# and the item degraded to indeterminate, gating the artifact. But the artifact
# is a full-viewport WebGL volcano with `overflow: hidden`: there is nothing to
# scroll, BY DESIGN. Its wheel handler calls `preventDefault()` and moves the
# camera. Position is the wrong instrument -- it can only ever read zero on the
# entire class of artifact this suite exists to verify.
#
# Measured on three pages, same wheel:
#
#   page                  pos moved   preventDefault   DOM text changed
#   canvas zoom app       False       yes              yes ("RANGE 1841"->"1691")
#   inert page            False       no               no
#   scrollable document   True        no               no
#
# The three separate perfectly. So a wheel counts as having had an effect when
# the position moved (as before) OR the app consumed it OR the DOM changed in
# response -- and only the genuinely inert page still reports the error, which is
# the one case where reporting it was ever right.
#
# `preventDefault` is patched on `Event.prototype` rather than read via
# `defaultPrevented` in a listener, because a listener's answer depends on
# registration order against the app's own: attach on window and an app that also
# listens on window, registered first, has already run. Patching the method
# catches the call wherever it happens. The patch is scoped to wheel events,
# records rather than suppresses (the original is always invoked), and is removed
# in `_wheel_consumed` so nothing outlives the step.
_WHEEL_WATCH_ARM_JS = """() => {
  if (window.__ivWheelPatched) { window.__ivWheelPrevented = 0; return; }
  window.__ivWheelPatched = true;
  window.__ivWheelPrevented = 0;
  const orig = Event.prototype.preventDefault;
  window.__ivWheelOrig = orig;
  Event.prototype.preventDefault = function () {
    if (this && this.type === 'wheel') { window.__ivWheelPrevented++; }
    return orig.apply(this, arguments);
  };
}"""

_WHEEL_WATCH_READ_JS = """() => {
  const n = window.__ivWheelPrevented || 0;
  if (window.__ivWheelPatched && window.__ivWheelOrig) {
    Event.prototype.preventDefault = window.__ivWheelOrig;
    window.__ivWheelPatched = false;
    window.__ivWheelOrig = null;
  }
  return n;
}"""

# Second signal, for a handler that moves a camera without preventing the
# default. Body text is the cheap, already-proven instrument (the readouts these
# artifacts print beside their canvas -- "RANGE 1841", "1 FPS" -- are exactly what
# the item is asking about). Truncated because some artifacts print a whole
# telemetry wall, and only equality matters.
_SCROLL_TEXT_JS = """() => (document.body ? document.body.innerText : '').slice(0, 4000)"""


def _wheel_watch_arm(page: Any) -> None:
    """Start recording `preventDefault()` on wheel events. Never raises."""
    try:
        _bounded_page_eval(page, _WHEEL_WATCH_ARM_JS, default=None)
    except Exception:  # noqa: BLE001 - stub page, closed context, CSP
        pass


def _wheel_consumed(page: Any) -> int:
    """How many wheel `preventDefault()` calls fired, unpatching. Never raises."""
    try:
        n = _bounded_page_eval(page, _WHEEL_WATCH_READ_JS, default=0)
    except Exception:  # noqa: BLE001
        return 0
    return int(n) if isinstance(n, (int, float)) else 0


def _body_text(page: Any) -> str | None:
    """Truncated body text, or None if unreadable. Never raises."""
    try:
        txt = _bounded_page_eval(page, _SCROLL_TEXT_JS, default="")
    except Exception:  # noqa: BLE001
        return None
    return txt if isinstance(txt, str) else None

# Fallback: scroll the dominant scroll container directly. Prefers the deepest
# element under the viewport centre that can actually scroll — an inner scroller
# like `#scroll-track` — and only falls back to the document when there is none.
# `scrollTop`/`scrollTo` are synchronous and unaffected by whatever swallowed the
# wheel, and `scroll-behavior: smooth` is bypassed with an explicit `instant`.
_SCROLL_FALLBACK_JS = """(dy) => {
  const scrollable = e => {
    if (!e || e === document.documentElement || e === document.body) return false;
    const s = getComputedStyle(e);
    return (e.scrollHeight - e.clientHeight) > 4
        && /(auto|scroll|overlay)/.test(s.overflowY);
  };
  let el = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
  while (el && !scrollable(el)) el = el.parentElement;
  if (!el) {
    let best = 4;
    document.querySelectorAll('*').forEach(e => {
      const over = e.scrollHeight - e.clientHeight;
      if (over > best && scrollable(e)) { best = over; el = e; }
    });
  }
  if (el) { el.scrollTop += dy; return 'element:' + (el.id || el.tagName); }
  window.scrollTo({top: window.scrollY + dy, behavior: 'instant'});
  return 'window';
}"""


def _scroll_pos(page: Any) -> list[int] | None:
    """``[scrollY, scrollX, sum-of-element-scrollTops]``, or None if unreadable."""
    try:
        pos = _bounded_page_eval(page, _SCROLL_POS_JS, default=None)
    except Exception:  # noqa: BLE001  (stub page, closed context, CSP)
        return None
    return list(pos) if isinstance(pos, (list, tuple)) and len(pos) == 3 else None


def execute_scroll(page: Any, action: dict[str, Any]) -> dict[str, Any]:
    """Wheel, verify it had an effect, and fall back to a scripted scroll.

    Returns the ``browser_actions``-shaped result with a ``scroll`` key carrying
    the before/after positions and the method that finally worked. An unreadable
    position (stub page) is not an error — the wheel is dispatched and the result
    passed through unchanged, exactly as before.

    "Effect" is three signals, not one (DEFECT 57): the scroll position moved, or
    the app consumed the wheel with ``preventDefault()``, or the DOM reacted. A
    full-viewport 3D viewer can only ever satisfy the last two, and reporting it
    as a no-op cost `xXbgiFY` its critical camera item.
    """
    before = _scroll_pos(page)
    before_txt = _body_text(page)
    _wheel_watch_arm(page)
    res = browser_actions.execute(page, action)
    if before is None or "error" in res:
        _wheel_consumed(page)  # unpatch; the step is over either way
        return res
    dy = action.get("dy")
    dy = int(dy) if isinstance(dy, (int, float)) else 400
    try:
        page.wait_for_timeout(250)
    except Exception:  # noqa: BLE001
        pass
    after = _scroll_pos(page)
    # DEFECT 57: read the consumption signals BEFORE deciding, and always -- the
    # patch must come off whichever branch is taken.
    consumed = _wheel_consumed(page)
    after_txt = _body_text(page)
    reacted = (before_txt is not None and after_txt is not None
               and after_txt != before_txt)
    if after is not None and after != before:
        return {**res, "scroll": {"before": before, "after": after,
                                  "method": "wheel", "moved": True}}
    if consumed or reacted:
        # The page never scrolled because it is not a scrolling page -- a
        # full-viewport 3D viewer that turns the wheel into camera distance. The
        # wheel was received and acted on, which is precisely what the item asks
        # about, so this is a successful interaction and not a no-op.
        why = "consumed" if consumed else "dom-changed"
        return {**res, "scroll": {"before": before, "after": after,
                                  "method": f"wheel:{why}", "moved": True,
                                  "wheel_prevented": consumed,
                                  "dom_reacted": reacted}}
    method = "wheel"
    try:
        method = f"fallback:{_bounded_page_eval(page, _SCROLL_FALLBACK_JS, dy, default='timeout')}"
        page.wait_for_timeout(250)
        after = _scroll_pos(page)
    except Exception as exc:  # noqa: BLE001
        method = f"fallback-failed:{type(exc).__name__}"
    moved = after is not None and after != before
    out = {**res, "scroll": {"before": before, "after": after,
                             "method": method, "moved": moved}}
    if not moved:
        # Nothing moved by ANY means: the page is not scrollable (or refuses to
        # be). Report it as an error so the step is treated as a no-op — its
        # before/after pair is two identical frames, and letting the judge read
        # that as "the app ignored the scroll" is the false negative this whole
        # function exists to prevent.
        out["error"] = (f"scroll had no effect (dy={dy}, pos unchanged at "
                        f"{before}, wheel not consumed, DOM unchanged, "
                        f"tried {method})")
        out.pop("result", None)
    return out


_SELECTOR_VERBS = {"click", "hover", "type", "select"}

# Ceiling for a single selector action's auto-wait. Deliberately short and
# decoupled from step_timeout_s (which governs `wait` durations): an element that
# is present but not yet actionable — enabled, stable, hit-testable — reaches that
# state within a second or two, so a longer wait only pays out on elements that
# will NEVER be actionable (e.g. a button visually covered by a full-screen WebGL
# canvas that swallows pointer events). Those are the dominant executor cost on
# heavy 3D pages; bounding them here turns a 15–30s dead-wait into ~2.5s.
ACTION_TIMEOUT_MS = 2500

# ...with one exception, and it is not the occlusion case above. A button that is
# `disabled` until the app finishes booting is not "never actionable" — it is
# not actionable YET, and the wait pays out. `dLmjAh5` ships its entry button as
# `<button id="gDemo" disabled>` and enables it only after the WebGL scene
# builds (sculpture.js:1845). Measured on an idle machine it enables at 2.07s —
# inside the ceiling by 0.4s, which under lane contention it does not survive. The
# click timed out, the fallback landed on an occluded twin, and a correct
# artifact was judged to produce no audio.
#
# So: retry ONCE with a longer budget when the call log says the element resolved
# but was not enabled. That is a state the page can leave on its own, unlike
# occlusion or a never-settling animation, so the extra wait is bounded work with
# a real chance of paying off rather than a dead-wait on every heavy 3D page.
ACTION_RETRY_TIMEOUT_MS = int(os.environ.get("IV_ACTION_RETRY_TIMEOUT_MS", "8000"))

# How many of the page's own frames one Playwright click costs. Measured, not
# guessed: five b0811 artifacts, each on an IDLE box, one click on a plain
# enabled button, alongside the page's rAF cadence.
#
#   task      fps   frame    click    frames
#   dLmjAh5   3.5   286ms    3.81s      13
#   Dfxc2vB   5.7   175ms    3.10s      18
#   FGfZjZb   5.3   189ms    2.32s      12
#   vJJzx4N   3.3   303ms    2.97s      10
#
# The handshake is a sequence of main-thread round trips — resolve the locator,
# check visible/enabled/stable (twice, since stability means two agreeing
# frames), scroll into view, hit-test, dispatch — and each one waits for the
# renderer to come back. So a click's cost is a FRAME COUNT, and its wall time is
# that count times the page's frame period. A heavy WebGL artifact painting at
# 3 fps therefore needs ~3-6s for a click that is in no way unhealthy.
#
# ACTION_TIMEOUT_MS = 2500 is below that floor for every one of the five. This is
# what fix9's unclassifiable timeouts were: not a pegged renderer, not contention
# (the box was idle at 0.4 of 8 cores), just a fixed budget applied to a page
# whose frames are 5-10x longer than the 60fps page the constant was chosen on.
# Sixteen items on dLmjAh5 and Dfxc2vB went indeterminate on it.
#
# 20 frames covers the worst observed (18) with margin. The floor keeps the
# original 2500ms for normal pages — this may only ever RAISE the budget, never
# lower it — and the ceiling stops a 0.5fps page from reinstating the dead-wait
# ACTION_TIMEOUT_MS exists to bound.
ACTION_TIMEOUT_FRAMES = 20
ACTION_TIMEOUT_MAX_MS = 12000
# THE BUDGET IS MONOTONE IN THE RATE: a slower page never gets less than a faster
# one, all the way down. There is no stall threshold anywhere on this path, and its
# absence is the whole of v0.10.
#
# v0.9 had one — MIN_MEASURABLE_FPS = 1.0, below which a page was called "stalled"
# and handed the FLOOR, so a wedged renderer could not claim the 12000ms ceiling.
# The reasoning was sound and the number was calibrated on an idle box against
# synthetic fixtures. Then it met production. Under the real 3-worker load on our
# 8-core cgroup, `dLmjAh5` paints at ~1.0fps — not wedged, just contended, since
# load slows the page and our own click handshake together — and three concurrent
# reads of the same artifact landed on both sides of the line:
#
#     worker   measured fps   budget
#       w1         0.97         2500   <- floor
#       w2         1.03        12000
#       w3         1.00        12000
#
# 0.06fps of scheduler noise, 4.8x of budget, in the wrong direction. That is the
# fix10 inversion for the third time (defect 29 was the second): the page least
# able to afford a short budget is the one most likely to be handed it. And it
# cascaded — the retry is `action_timeout_ms * ACTION_RETRY_TIMEOUT_MS /
# ACTION_TIMEOUT_MS` = 3.2x, so a floored budget produces exactly the 8000ms retry
# that dLmjAh5's step 6 timed out on. One bad read, both failures.
#
# The first repair moved the cliff rather than removing it: discriminate a wedge by
# whether the page can ANSWER the read inside FPS_READ_TIMEOUT_MS. Measurement
# refused that too. The read's own cost is ~8.5 frames, so "can it answer in 9s"
# is just "is it above ~0.94fps" wearing a different hat — the same continuum, the
# same cliff, moved by 0.06fps. The 0.83fps fixture proved it by landing on both
# sides across runs (9.66s answered, then timed out).
#
# So the threshold is gone entirely, and what justifies that is a measurement of
# what these pages actually need. At ~1.0fps under 3-worker load, `dLmjAh5`'s
# ENGAGE click SUCCEEDS in 10.1-10.8s, three runs out of three, at the 12000ms
# ceiling — and fails at anything smaller. A page at 1fps is not stuck; it is a
# heavy WebGL scene on a contended box, and the ceiling is the correct budget for
# it. The truly-wedged renderer that motivated the threshold has never appeared in
# a production trajectory; it was a fixture we wrote.
#
# What bounds a genuine wedge is not this function. ACTION_TIMEOUT_MAX_MS caps any
# single action at 12s, and `_deadline`/exec_budget_s caps the trajectory — so a
# stalled page costs a bounded number of 12s dead-waits and the run ends. That is
# a real cost, and it is the RIGHT trade: it is paid only by pages that are
# actually stuck, whereas the threshold was paid by every slow-but-healthy page in
# the set, silently, as zero scores. Bound the damage where it is cheap to bound,
# not by guessing which pages deserve it from a number that noise can move.
#
# THE TRANSFERABLE RULE: a threshold on a measured quantity is a cliff, and a cliff
# placed where real values live will be fallen off. Before adding one, measure
# where the population actually sits. If it sits on the line, there is no line to
# draw — bound the consequence instead.
# Cap on the ONE read, and it is set from the read's own measured cost. Routing
# through `_bounded_eval` buys a bound that `page.evaluate` cannot give (that call
# takes no timeout and blocks as long as the animation loop runs), but the bound is
# paid in frames like everything else. Measured, install-at-init then read after a
# 5s settle:
#
#     true fps   read      frames
#       59.93    0.14s      8.4
#        3.32    2.53s      8.4
#        1.42    6.25s      8.9
#        0.83   10.63s      8.8
#        0.50   17.02s      8.5
#      wedged    (cap)        -
#
# ~8.5 frames, flat. So a cap of N ms can measure down to ~8500/N fps and no
# further: below that the read times out and the page takes the floor by the null
# path. That is a cliff too — it is the same continuum as the deleted
# MIN_MEASURABLE_FPS, just expressed in milliseconds — so this number must sit well
# BELOW where real artifacts live, not near them. 9000ms reaches ~0.94fps; the
# slowest production artifact measured is `dLmjAh5` at ~1.0fps under 3-worker load,
# whose ENGAGE click needs 10.1-10.8s and gets it. A 2000ms cap — the first cut —
# reached only 4.2fps and handed that same artifact the floor at 3.3fps.
#
# Lowering this constant therefore RAISES the effective stall threshold, and every
# page between the new one and the old silently gets the floor while healthy. That
# is the fix10 inversion, and it is why the cap is 9000 and not a rounder, smaller
# number. `_smoke_frame_budget` check 0b pins the relationship; do not change this
# without reading it.
FPS_READ_TIMEOUT_MS = 9000

# Sample the page's real frame cadence. rAF is the honest instrument: it fires
# when the renderer actually paints, so it measures what a click will have to
# wait for.
#
# Installed as a free-running COUNTER rather than sampled on demand, and that is
# the whole design. The first cut opened a 1200ms rAF window inside a bounded
# `evaluate` with a 3000ms cap, and fix10 showed why that cannot work: the probe's
# own cost is paid in the same currency it is measuring. Reaching the page, the
# 1200ms window, and getting the answer back are all main-thread round trips at
# the page's frame period, so on a 3fps page under 3-worker load the probe exceeds
# its own cap — and a failed probe returns the FLOOR. The slower the page, the
# likelier the measurement fails, and failure hands the smallest budget to the
# page that needed the largest. `dLmjAh5` recorded action_timeout_ms=2500 in fix10
# where an idle-box probe of the same artifact returns 5458, lost the ENGAGE click
# again, and fell from 4 determinate items to 0.
#
# A counter inverts that. `_FPS_INSTALL_JS` starts one rAF chain; reading it later
# is a single property read that returns whatever has accumulated, so the read
# costs one round trip instead of one round trip plus 1200ms of waiting. And the
# reading is BETTER, not merely cheaper: it averages over however long the page
# has actually been running, which is the steady state the actions will meet,
# rather than one 1.2s window that may land during a load spike.
#
# It is installed via `add_init_script`, BEFORE navigation, and that detail is
# load-bearing rather than tidy. Installing by `evaluate` after load is itself a
# main-thread round trip, and measurement showed it inherits the same disease at
# the same page speeds: on a fixture painting at 0.8fps the install `evaluate`
# burned its full 2000ms cap and the counter never existed. An init script is
# delivered over CDP before any page JS runs, costs nothing at page time, and is
# already ticking by the time the first frame paints — so the read later is the
# only round trip on this path, and the pages hardest to measure are exactly the
# ones that now get measured.
#
# What it keeps is a RING of the last FPS_WINDOW frame timestamps, not a lifetime
# count, and that correction came from the artifact this was built for. A lifetime
# average silently includes the fast frames a page paints before its scene exists:
# on `dLmjAh5` the first cut read 8.27fps three seconds after load, while the gate
# screen the actions actually meet runs at 3.8fps — so it was handed the 2500ms
# floor and lost the ENGAGE click again, the fix10 failure reproduced through a
# different mechanism. A ring reports the cadence NOW, which is the cadence the
# next click will pay, and it costs the same single property read.
_FPS_INSTALL_JS = """
  if (!window.__ivFps) {
    const N = __N__;
    const s = window.__ivFps = {t: new Array(N), i: 0, n: 0, N: N};
    const tick = () => {
      s.t[s.i] = performance.now();
      s.i = (s.i + 1) % N; s.n++;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
"""

# How many recent frames the rate is computed over. 20 spans ~5s on a 4fps page —
# long enough that one slow frame does not dominate — and ~0.3s at 60fps, which is
# fine because a fast page only has to be recognised as fast enough for the floor.
FPS_WINDOW = 20

# Read the recent rate off the ring. Returns null when the counter was never
# armed, or before it holds enough frames to divide by: under 4 samples the
# interval estimate is noise, and the caller treats null as "use the floor".
_FPS_READ_JS = """() => {
  const s = window.__ivFps;
  if (!s) return null;
  const m = Math.min(s.n, s.N);
  if (m < 4) return null;
  const newest = s.t[(s.i - 1 + s.N) % s.N];
  const oldest = s.n < s.N ? s.t[0] : s.t[s.i % s.N];
  const span = newest - oldest;
  return span > 0 ? (m - 1) / (span / 1000) : null;
}"""

_FPS_INSTALL_JS = _FPS_INSTALL_JS.replace("__N__", str(FPS_WINDOW))


FPS_INSTALL_TIMEOUT_S = float(os.environ.get("IV_FPS_INSTALL_TIMEOUT_S", "3.0"))


def install_fps_counter(page: Any) -> None:
    """Arm the rAF counter `_frame_scaled_timeout_ms` reads. Best-effort.

    Must be called BEFORE navigating: `add_init_script` applies to the next
    document, not the current one. Failure is survivable by design — the read
    returns the floor when no counter is found — but see `_FPS_INSTALL_JS` for
    why the `evaluate` route is not an acceptable fallback here.

    DEFECT (b0811 `miniapp_interactive-art_generati__a2YwBBP`, Scroll Canyon
    Garden): the bare `page.add_init_script` call is another unbounded Playwright
    sync operation and can wedge indefinitely between the planner phase (which
    leaves the page rendering a WebGPU/WebGL2 scene) and the executor's own
    `navigate`. Greenlet dump at 145s past load showed the worker parked at
    exactly `page.add_init_script`. `add_init_script` is nominally a browser-side
    bookkeeping call, but on a page whose renderer is continuously pegged the CDP
    response never returns and the sync bridge parks forever — the same DEFECT-120
    hazard class that `axtree.capture`, `_cdp_screenshot`, `capture_axtree`, and the
    `query_selector_all` sites have all been bounded for.

    Bound it via a daemon thread with `FPS_INSTALL_TIMEOUT_S`. On timeout the
    counter is simply not installed and `_frame_scaled_timeout_ms` degrades to
    ACTION_TIMEOUT_MS — that is the SAME thing that happens today when the call
    raises for a stub/closed page, so this fix only widens the class of failures
    the docstring's "survivable by design" promise already covers. The abandoned
    daemon thread holds a dead sync-boundary reference but cannot block exit; it
    exits with the browser tree.
    """
    box: dict[str, Any] = {}

    def _work() -> None:
        try:
            page.add_init_script(_FPS_INSTALL_JS)
            box["ok"] = True
        except Exception:  # noqa: BLE001 — stub page, closed context, CDP timeout
            box["ok"] = False

    th = threading.Thread(target=_work, name="fps-install", daemon=True)
    th.start()
    th.join(FPS_INSTALL_TIMEOUT_S)
    # Silently degrade whether we hit an exception, a timeout, or (rare) a stub
    # page — the downstream read returns the floor when the counter is missing,
    # which is the same behaviour as before this bound was added.


def _frame_scaled_timeout_ms(page: Any) -> int:
    """ACTION_TIMEOUT_MS, raised to fit this page's frame period.

    Returns the floor (ACTION_TIMEOUT_MS) on any failure, on a fast page, on a
    page where `install_fps_counter` never ran, and on a stub page without
    `evaluate` — this must never make a budget smaller than the one it replaces,
    or it becomes a new way to lose steps.

    Reads the counter armed at init rather than sampling here; see
    `_FPS_INSTALL_JS` for why sampling on demand fails on exactly the pages this
    exists for. The read is one property access, but it is still a main-thread
    round trip and so still costs frames (~8.5, measured) — hence
    FPS_READ_TIMEOUT_MS rather than a small round number.

    There is deliberately NO stall threshold: the budget is monotone in the rate,
    all the way down. v0.9 had one and it fired on a healthy page (see the block
    above MIN_MEASURABLE_FPS's grave). A genuinely wedged renderer is bounded by
    ACTION_TIMEOUT_MAX_MS per action and by exec_budget_s per trajectory, not by
    guessing from a number that scheduler noise can move by 5%.
    """
    try:
        fps = browser_actions._bounded_eval(page, _FPS_READ_JS,
                                           timeout_ms=FPS_READ_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - closed page, or a stub without evaluate
        return ACTION_TIMEOUT_MS
    if not isinstance(fps, (int, float)) or isinstance(fps, bool):
        return ACTION_TIMEOUT_MS
    if float(fps) <= 0:
        return ACTION_TIMEOUT_MS
    want = int(ACTION_TIMEOUT_FRAMES * 1000.0 / float(fps))
    return max(ACTION_TIMEOUT_MS, min(want, ACTION_TIMEOUT_MAX_MS))


# Playwright names the actionability failure in its call log. These two cases
# must be told apart because they demand opposite responses: a covered element
# must NOT be force-clicked (the event would land on the overlay and report a
# success that never happened), while a still-booting one just needs longer.
_OCCLUDED_RE = re.compile(r"intercepts pointer events", re.I)
_NOT_ENABLED_RE = re.compile(r"not enabled|element is disabled", re.I)

# A third failure class the ladder above did not model. When the selector never
# resolves at all, Playwright's call log is a bare `waiting for locator(...)`
# with no actionability line after it — the element was not in the DOM within
# the budget. That is NOT "disabled but booting" (the element exists) and NOT
# occlusion (something is painted over it); it is a page that had not built the
# control yet. It used to fall through to the force branch, which cannot help:
# force still has to resolve the locator, so it failed identically and the step
# was lost.
#
# Measured on `dLmjAh5` under load: `click button:has-text("ENGAGE — DEMO
# SIGNAL")` timed out at 2500ms with a bare `waiting for locator(...)`, where an
# unloaded run resolved the same selector to `<button disabled id="gDemo">` and
# was rescued by the _NOT_ENABLED_RE re-wait. The gate was never dismissed, so
# all 12 items came back indeterminate and the task scored 0.0. One artifact's
# entire verdict hung on a control that needed another second to appear.
#
# So: an unresolved locator gets the same one longer re-wait as a disabled one.
# The two cases are the same bet — the page can fix this by itself, given time.
# A genuinely absent control still fails, just ACTION_RETRY_TIMEOUT_MS later,
# and only on steps that already failed.
_UNRESOLVED_RE = re.compile(r"waiting for locator", re.I)


# DEFECT 64. Occlusion has a sub-case the "never force" rule reads as fatal but
# which is really an AMBIGUOUS SELECTOR, and b0811 `re9S4An` lost three items to
# it — the user's "fail to create objects on canvas":
#
#     Page.click: Timeout 6651ms exceeded.
#     Call log:
#       - waiting for locator("canvas")
#         - locator resolved to 3 elements. Proceeding with the first one:
#           <canvas id="gl" width="950" height="502"></canvas>
#       - attempting click action
#         2 x waiting for element to be visible, enabled and stable
#           - element is visible, enabled and stable
#           - <canvas id="ov" tabindex="0" aria-label="Tunnel simulation
#             viewport..."></canvas> intercepts pointer events
#
# This is the layered-canvas idiom: `#gl` is the WebGL render surface and `#ov`
# is the transparent interaction overlay stacked on top of it, which is where
# every listener lives (note the tabindex and the aria-label — `#ov` is the
# control, `#gl` is the picture). The plan said `canvas`, THREE elements matched,
# and Playwright's documented behaviour is to take the first in document order.
# It took the picture. The overlay then correctly refused the click.
#
# Declining is right and forcing is wrong — a forced click on `#gl` dispatches at
# a surface with no listener and reports `ok`, the fake success the occlusion
# guard exists to prevent. But there is a third answer neither branch offered:
# CLICK THE OCCLUDER, when the occluder is itself one of the elements the plan's
# own selector matched.
#
# That condition is what makes this safe, and it is the whole rule. We are not
# guessing a better target: we are choosing a different element from the set the
# plan already named, after Playwright told us its arbitrary first pick was the
# wrong one of the N. A retarget outside that set would be a click on a control
# nobody asked for; this one cannot be, because the plan's selector matches it.
#
# It generalises past canvases — stacked overlays, a `.btn` matching a label and
# the real button painted over it, any duplicate-match where the top of the
# z-stack is the live one. The idiom is common because it is the natural way to
# separate rendering from input, and a verifier that cannot click through it
# cannot test any artifact built that way.
_HIT_MATCHED_JS = """([els]) => {
  if (!els || els.length < 2) return -1;
  const first = els[0];
  const r = first.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0)) return -1;
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const hit = document.elementFromPoint(x, y);
  if (!hit || hit === first) return -1;
  // Only ever return an element the caller's own selector matched.
  for (let i = 1; i < els.length; i++) {
    if (els[i] === hit || els[i].contains(hit)) return i;
  }
  return -1;
}"""


def _occluding_sibling_match(page: Any, target: str) -> str | None:
    """Index-pinned selector for an occluder that the target selector matched.

    Returns None unless the situation is exactly the ambiguous-selector case:
    the selector matched more than one element, and the thing sitting at the
    first match's centre point is one of the OTHER matches. Anything else —
    a single match, an occluder from outside the matched set, a zero-sized
    first match — keeps the caller's existing behaviour, which is to decline
    the click and say so.

    The returned selector is `nth=`-pinned rather than rebuilt from the
    occluder's attributes, so it cannot drift to a different element than the
    one we hit-tested, and it works for Playwright-only selectors that
    `document.querySelector` could not parse.
    """
    els = _bounded_query_all(page, target)
    if not els or len(els) < 2:
        return None
    try:
        idx = page.evaluate(_HIT_MATCHED_JS, [els])
        if not isinstance(idx, int) or idx < 1:
            return None
        return f"{target} >> nth={idx}"
    except Exception:  # noqa: BLE001 - a stub page is not an error
        return None


# DEFECT 70. An overlay OPENED BY AN EARLIER STEP blocks every step after it, and
# nothing in the pipeline closes one. Two existing mechanisms look like they
# should cover this and neither does:
#
#   * `dismiss_entry_gate` runs ONCE, before the first action, against the
#     artifact's boot splash. It cannot see a dialog that step 32 opened.
#   * `_occluding_sibling_match` (DEFECT 64) only retargets when the occluder is
#     one of the SELECTOR'S OWN matches — the layered-canvas case. A tour scrim
#     is not in any button's matched set, so it correctly declines, and the step
#     dies with `[not force-clicked: target is occluded]`.
#
# Measured on `accept_d69`: 20 occluded steps over 3 tasks (5.5% of all steps),
# 15 of them DEFECT-66 back-fill probes appended after the plan's own sequence —
# the worst place to be blocked, because those probes are the last chance an item
# has to acquire evidence. Every occluder is a transient overlay a probe itself
# opened, across four independent idioms:
#
#     9nZimj5   9 steps  <div role="dialog" aria-modal="true" class="atx-tour">
#                        opened by step 32's own click on "Start tour"
#     9eLur2v   6 steps  <div class="modal-scrim">
#     BxrjKKx   3 steps  <nav id="menu" class="menu is-on" aria-hidden="false">
#     (d63/d68) 1 step   <div class="atx-root"> — the assistant chat panel
#
# The damage is item-level and large: `9nZimj5` fell 10/12 determinate to 3/12,
# with items 4,5,6,7,8,9,10,12 all losing their verdict to this one cause. It is
# a pure verifier defect — the artifact is fine, the overlay is DISMISSIBLE, and
# a human tester would press Escape without thinking about it.
#
# So dismiss it, using only what the artifact itself offers, in the order a user
# would try:
#   1. Escape on the focused document — the documented close for `aria-modal`
#      dialogs, and what `9nZimj5` binds (`if (e.key !== 'Escape') return; if
#      (self.tourState) self.stopTour('escape')`).
#   2. The overlay's own dismiss control — a button whose accessible name reads
#      close/skip/dismiss/cancel/done/got it/×. `9nZimj5`'s tour ships `Skip`.
#   3. The scrim itself, when the overlay has one — `on(scrim, 'click',
#      function () { self.stopTour('scrim') })` is the third binding the same
#      artifact provides, and the near-universal idiom.
#
# Two hard constraints keep this from becoming a wrong-click machine:
#
#   (a) It runs ONLY from the occlusion branch, i.e. only after Playwright has
#       already told us a specific element is intercepting the click. We never
#       hunt for overlays speculatively, so an artifact that never occludes
#       anything never executes a line of this.
#   (b) It only ever clicks INSIDE the reported occluding subtree, never in the
#       artifact's content. A dismissal that clicked a content control would
#       manufacture exactly the fake success the occlusion guard exists to
#       prevent — and worse, it would do so silently.
#
# If the overlay survives all three, behaviour is unchanged: decline, and record
# the occlusion honestly. An overlay that cannot be dismissed by Escape, its own
# close button, or its scrim is a real finding about the artifact.
_DISMISS_OVERLAY_JS = r"""() => {
  // Find what is painted over the page and, if it is dismissible, name a
  // clickable way out. Marks its picks with data-iv-ovl so the caller can click
  // them by a stable selector; pure read otherwise.
  const NAME_RE = /^(?:close|skip|dismiss|cancel|done|no thanks|not now|got it|ok|okay|finish|exit|×|✕|✖|x)\b/i;
  const OVL_SEL =
    '[role="dialog"],[role="alertdialog"],[aria-modal="true"],' +
    '[class*="modal"],[class*="scrim"],[class*="overlay"],[class*="backdrop"],' +
    '[class*="tour"],[class*="lightbox"],[class*="drawer"],nav,dialog';
  const vw = innerWidth || 1, vh = innerHeight || 1;
  const painted = (e) => {
    const s = getComputedStyle(e);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    if (parseFloat(s.opacity || '1') < 0.02) return false;
    if (s.position !== 'fixed' && s.position !== 'absolute') return false;
    if (s.pointerEvents === 'none') return false;
    const r = e.getBoundingClientRect();
    if (r.width < 24 || r.height < 24) return false;
    // Must actually overlap the viewport to be in anyone's way.
    return !(r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw);
  };
  const seen = [];
  for (const e of document.querySelectorAll(OVL_SEL))
    if (painted(e)) seen.push(e);
  if (!seen.length) return null;
  // Innermost-last: prefer the deepest overlay, which is the topmost one when
  // several are stacked (a tour over a modal over a drawer).
  let root = seen[seen.length - 1];
  // ...then climb OUT to the whole overlay. The deepest match is frequently the
  // SCRIM (`.modal-scrim` matches `[class*="scrim"]`), and stopping there would
  // scope the close-button search to a bare backdrop with no children — missing
  // the `Skip` button that lives in its sibling card. An explicit dialog role
  // wins outright; otherwise take the outermost still-painted overlay ancestor.
  const dlg = root.closest('[role="dialog"],[role="alertdialog"],[aria-modal="true"],dialog');
  if (dlg && painted(dlg)) {
    root = dlg;
  } else {
    for (let e = root.parentElement; e && e !== document.body; e = e.parentElement) {
      if (e.matches(OVL_SEL) && painted(e)) root = e;
    }
  }
  const out = {
    root: (root.id ? '#' + root.id : root.tagName.toLowerCase()) +
          '.' + String(root.className || '').slice(0, 40),
    modal: !!(root.getAttribute &&
              (root.getAttribute('aria-modal') === 'true' ||
               root.getAttribute('role') === 'dialog')),
    closer: null, scrim: null,
  };
  // A dismiss control INSIDE the overlay. Accessible name first, then the
  // conventional attribute hooks. Never looks outside `root`.
  const btns = root.querySelectorAll(
    'button,[role="button"],a[href="#"],[class*="close"],[class*="skip"],' +
    '[data-close],[data-dismiss],[aria-label]');
  for (const b of btns) {
    const s = getComputedStyle(b);
    if (s.display === 'none' || s.visibility === 'hidden') continue;
    const r = b.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) continue;
    const name = ((b.getAttribute('aria-label') || '') + ' ' +
                  (b.innerText || b.value || '') + ' ' +
                  (b.getAttribute('title') || '')).trim();
    const hook = b.hasAttribute('data-close') || b.hasAttribute('data-dismiss') ||
                 /close|skip|dismiss/i.test(String(b.className || ''));
    if (NAME_RE.test(name) || hook) {
      b.setAttribute('data-iv-ovl', 'closer');
      out.closer = true;
      break;
    }
  }
  // The scrim: an element covering most of the viewport that carries no content
  // of its own. Clicking it is the documented "click outside to close" gesture.
  //
  // `root` ITSELF is a candidate, not just its descendants — an overlay is
  // frequently a single bare `<div class="modal-scrim">` with the card as a
  // SIBLING, and searching only inside it finds nothing. The no-text/no-children
  // test is what keeps this safe either way: it means we click a blank backdrop,
  // never a card, a heading, or a control. And because the candidate is `root`
  // or inside it, it can never be the artifact's own content.
  let best = null, bestArea = 0;
  for (const c of [root, ...root.querySelectorAll('*')]) {
    const s = getComputedStyle(c);
    if (s.pointerEvents === 'none') continue;
    if (String(c.textContent || '').trim().length) continue;
    if (c.children.length) continue;
    const r = c.getBoundingClientRect();
    const frac = (r.width * r.height) / (vw * vh);
    if (frac > 0.5 && frac > bestArea) { bestArea = frac; best = c; }
  }
  if (best) { best.setAttribute('data-iv-ovl', 'scrim'); out.scrim = true; }
  return out;
}"""


# How long an overlay's close transition is given before we re-probe. A modal
# that fades out over 300ms is still intercepting at 100ms, and re-probing too
# early would read a successful dismissal as a failure and move on to the next
# tier — clicking a scrim that was already on its way out.
_OVERLAY_SETTLE_MS = 350


def _dismiss_blocking_overlay(page: Any) -> str | None:
    """Close a transient overlay that is intercepting clicks. DEFECT 70.

    Returns a short provenance string naming which dismissal worked (for the
    step note), or None if there was nothing to dismiss or nothing worked. Only
    ever clicks inside the overlay's own subtree — see `_DISMISS_OVERLAY_JS` and
    constraint (b) in the block above. Never raises: any probe failure means the
    caller keeps its existing decline-and-report behaviour.
    """
    try:
        hit = _bounded_page_eval(page, _DISMISS_OVERLAY_JS, default=None)
    except Exception:  # noqa: BLE001 — stub page, closed context, CSP
        return None
    if not isinstance(hit, dict):
        return None

    def _gone() -> bool:
        """True if the overlay we identified is no longer intercepting."""
        try:
            return not _bounded_page_eval(page, _DISMISS_OVERLAY_JS, default=None)
        except Exception:  # noqa: BLE001
            return False

    # 1. Escape. Free, and the documented close for an `aria-modal` dialog.
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(_OVERLAY_SETTLE_MS)
        if _gone():
            return "escape"
    except Exception:  # noqa: BLE001
        pass
    # 2. The overlay's own close/skip control.
    if hit.get("closer"):
        for how in ("click", "dispatch"):
            try:
                if how == "click":
                    page.click("[data-iv-ovl='closer']", timeout=1200, force=True)
                else:
                    page.dispatch_event("[data-iv-ovl='closer']", "click",
                                        timeout=1200)
                page.wait_for_timeout(_OVERLAY_SETTLE_MS)
                if _gone():
                    return "close control"
            except Exception:  # noqa: BLE001
                continue
    # 3. The scrim — "click outside to close".
    if hit.get("scrim"):
        try:
            page.click("[data-iv-ovl='scrim']", timeout=1200, force=True)
            page.wait_for_timeout(_OVERLAY_SETTLE_MS)
            if _gone():
                return "scrim"
        except Exception:  # noqa: BLE001
            pass
    _bounded_page_eval(
        page,
        "() => document.querySelectorAll('[data-iv-ovl]')"
        ".forEach(e => e.removeAttribute('data-iv-ovl'))",
        default=None)
    return None


def _never_resolved(err: str) -> bool:
    """True if the call log shows the locator itself never matched.

    Playwright logs `waiting for locator(...)` first and THEN the actionability
    checks it ran once the element existed ("element is visible, enabled and
    stable", "scrolling into view", "element is not enabled", ...). So the
    discriminator is not the presence of the waiting line — it is the absence of
    any line after it. Checking only for `waiting for locator` would classify
    every timeout as unresolved and hand a longer budget to the dead-wait cases
    ACTION_TIMEOUT_MS exists to bound.
    """
    if not _UNRESOLVED_RE.search(err):
        return False
    tail = err[_UNRESOLVED_RE.search(err).end():]
    return not re.search(r"^\s*-\s+\S", tail, re.M)


# A FOURTH class, found in fix8's Dfxc2vB and distinct from all three above. The
# locator resolved — the log names the element — and then Playwright logged
# nothing more:
#
#     Page.click: Timeout 2500ms exceeded.
#     Call log:
#       - waiting for locator("button:has-text(\"CONSOLE\")")
#         - locator resolved to <button id="rack-tab" aria-expanded="false" ...>
#
# It never reached `attempting click action`, so no actionability verdict exists:
# the element is not known to be disabled, unstable, or covered. Resolution alone
# consumed the whole budget. That happens when the renderer main thread is pegged
# — Dfxc2vB's own FPS readout showed a median of 1 FPS in that round against 6 in
# an unloaded one — because a `:has-text()` locator has to run a DOM query in the
# page, and a page that paints once a second answers slowly.
#
# `_never_resolved` deliberately excludes this (a `- locator resolved to` line IS
# a line after the waiting line, and treating a resolved element as absent would
# be wrong). But routing it to the force branch is worse than useless here: this
# was the CONSOLE disclosure toggle, and when the panel never opened, six drags
# on sliders inside it died on `KeyError: 'from'` and every slider item on the
# task went indeterminate. The step that lost the round was a click that nobody
# had established was unclickable.
#
# Same remedy as classes 1 and 3, same reasoning: more time is the one thing that
# helps, and only failed steps pay for it.
#
# The first cut of this keyed on the ABSENCE of `attempting click action`, and
# fix9 showed that boundary is in the wrong place. The same artifact produced a
# log one line longer:
#
#     Page.click: Timeout 2500ms exceeded.
#     Call log:
#       - waiting for locator("button:has-text(\"CONSOLE\")")
#         - locator resolved to <button id="rack-tab" aria-expanded="false" ...>
#       - attempting click action
#         - waiting for element to be visible, enabled and stable
#
# It DID attempt, so an attempt-keyed test rejected it — yet it is the identical
# failure: the actionability check was entered and never concluded, so there is
# still no verdict saying the element is disabled, unstable, or covered, and the
# fall-through still forces a click nobody established was needed. `attempting`
# is a line Playwright logs on the way in; the thing worth branching on is
# whether it ever came out. So the discriminator is the absence of a VERDICT:
#
#   - element is visible, enabled and stable      (pass)
#   - element is not enabled / not visible / not stable
#   - <div id="..."> intercepts pointer events
#
# Every one of those begins `element is ` or names an interceptor, and each is a
# statement about the element that a remedy can be chosen from. Absent all of
# them, the only fact in hand is "we ran out of time", and the only useful
# response is more of it.
#
# This also keeps the fifth shape out, which fix9 found on dLmjAh5 and which must
# NOT be re-waited: `element is visible, enabled and stable` -> `scrolling into
# view` -> `performing click action` -> timeout at 8000ms. That log has its
# verdict (the element was fine) and had already been given the long budget by
# this very ladder; the click itself is what the page would not accept. Handing
# it a third wait would spend the budget with nothing new to learn.
_RESOLVED_RE = re.compile(r"^\s*-\s+locator resolved to", re.I | re.M)
_VERDICT_RE = re.compile(r"element is |intercepts pointer events", re.I)


def _resolved_but_unattempted(err: str) -> bool:
    """True if the locator resolved but no actionability VERDICT was logged.

    Playwright's log order is `waiting for locator` -> `locator resolved to <el>`
    -> `attempting click action` -> `waiting for element to be visible, enabled
    and stable` -> a verdict. Class 4 is any timeout that dies before the verdict,
    whether or not it got as far as `attempting`: no actionability check
    concluded, so none of `_OCCLUDED_RE`, `_NOT_ENABLED_RE` or `_never_resolved`
    can match, and the ladder's fall-through (force) fires on an element whose
    clickability was never in question.

    Note `waiting for element to be visible, enabled and stable` deliberately
    does not count as a verdict — "to be" is the question, "is" is the answer.
    That one-word difference is what separates this class from the occluded and
    not-enabled cases, which must NOT be forced or blindly re-waited.
    """
    return bool(_RESOLVED_RE.search(err)) and not _VERDICT_RE.search(err)



# A SIXTH class, from b0811 `mKoh6BY`, and the one the ladder above handles
# worst. The locator resolved to the RIGHT element and the actionability check
# returned a clear verdict — the element is invisible:
#
#     Page.click: Timeout 2500ms exceeded.
#     Call log:
#       - waiting for locator("label:has-text(\"STREAMLINES\") input, ...")
#         - locator resolved to <input checked id="lStream" type="checkbox"/>
#       - attempting click action
#         2 x waiting for element to be visible, enabled and stable
#           - element is not visible
#
# Nothing is occluding it and it is not disabled, so none of classes 1-4 fire and
# it falls through to force. Force cannot help: a `display:none` element has no
# box to dispatch at, so the retry fails identically and the step is lost. Three
# of `mKoh6BY`'s eight indeterminate items died this way (STREAMLINES,
# ISO-PRESSURE, SMOKE TRACERS) — the user's report that "the execution agent
# failed to perform the action plan reliably".
#
# But the control is not broken and the artifact is not at fault: this is the
# ordinary styled-checkbox idiom, where the real `<input>` is hidden by CSS and
# the visible thing a user clicks is its `<label>`. A click on the label is
# forwarded to the input by the browser itself. So the remedy is not more time
# and not force — it is to click what a human would click.
_INVISIBLE_RE = re.compile(r"element is not visible", re.I)

# Runs against the RESOLVED element handle, not a selector string, because the
# target that resolved may be a Playwright-only selector (`:has-text(...)`) that
# `document.querySelector` cannot parse.
_LABEL_FOR_JS = """(el) => {
  const vis = (n) => {
    if (!n) return false;
    const st = getComputedStyle(n);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    if (parseFloat(st.opacity || '1') < 0.05) return false;
    const r = n.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  // Only the hidden-input idiom. A visible element that timed out is a
  // different failure and must not be silently retargeted somewhere else.
  if (vis(el)) return null;
  const tag = (el.tagName || '').toLowerCase();
  if (tag !== 'input' && tag !== 'select' && tag !== 'textarea') return null;
  const cands = [];
  if (el.id) {
    document.querySelectorAll('label[for]').forEach((l) => {
      if (l.getAttribute('for') === el.id) cands.push(l);
    });
  }
  const anc = el.closest ? el.closest('label') : null;
  if (anc) cands.push(anc);
  document.querySelectorAll('[data-iv-label-for]')
          .forEach((n) => n.removeAttribute('data-iv-label-for'));
  for (const l of cands) {
    if (!vis(l)) continue;
    l.setAttribute('data-iv-label-for', '1');
    return '[data-iv-label-for="1"]';
  }
  return null;
}"""


def _visible_label_for(page: Any, target: str) -> str | None:
    """Selector for the visible ``<label>`` driving a CSS-hidden form control.

    Returns None whenever the situation is anything other than the styled-input
    idiom — the element is visible after all, it is not a form control, or it
    has no label that a user could see. In every one of those cases the caller
    must keep its existing behaviour: a retarget we cannot justify is a click on
    a control nobody asked for, which is the fake-success failure the force
    branch already documents at length.
    """
    try:
        # Bounded query_selector — the pegged-renderer wedge is real here too.
        # page.evaluate below is element-anchored (needs `el` handle), so per the
        # plan's element-eval exclusion it stays unbounded — a rare path anyway.
        _hits = _bounded_query_all(page, target)
        el = _hits[0] if _hits else None
        if el is None:
            return None
        return page.evaluate(_LABEL_FOR_JS, el)
    except Exception:  # noqa: BLE001 - a stub page is not an error
        return None


def _selector_present(page: Any, target: str) -> bool:
    """True if ``target`` currently matches at least one element.

    A cheap, non-waiting existence probe (``query_selector`` returns immediately)
    so a planner-guessed selector that matches nothing fails in ~0ms instead of
    blocking on the click's auto-wait. Any probe failure (stub page, invalid
    selector syntax) returns True so the real action still runs and reports its
    own error — the probe must never suppress a would-be-valid action."""
    # `page.query_selector` is unbounded like its `_all` sibling — verified
    # wedged on the b0811 `miniapp_interactive-art_generati__a2YwBBP` renderer.
    # Route through `_bounded_query_all`, which returns [] on a pegged page (and
    # latches `_ax_too_slow` so later scans short-circuit): if the bounded scan
    # finds anything, the selector is present; if it times out we return True so
    # the real action still runs and reports its own error (unchanged pre-fix
    # behaviour for a probe failure).
    hits = _bounded_query_all(page, target)
    if hits is None:
        return True
    return bool(hits)


# Extracts the text from a Playwright ``:has-text("X")`` / ``:has-text('X')``
# predicate. Used to relax a planner selector that guessed the wrong tag/class.
_HAS_TEXT_RE = re.compile(r""":has-text\(\s*(["'])(.*?)\1\s*\)""")


def _relaxed_text_selector(target: str) -> str | None:
    """A tag/class-agnostic selector for the text in a ``:has-text`` predicate.

    Game main-menus are frequently built from styled ``<div>``/``<li>`` rows with
    a JS click listener and ``cursor:pointer`` — NOT semantic ``<button>``/``<a>``.
    The planner, reasoning from a screenshot, routinely guesses a plausible-but-
    wrong wrapper (``div.menu-item:has-text("Single Heat")``) when the live class
    is something else (``div.mrow``), so the exact selector matches nothing and
    the game never leaves its title screen — a verifier false-negative, not a
    broken build. When that happens we salvage the planner's INTENT (the visible
    label it wanted to click) by relaxing to ``:has-text("X") >> nth=-1``: match
    any element containing the text, innermost first (``nth=-1`` picks the deepest
    match — the actual row, not an ancestor container). Returns None when the
    selector carries no ``:has-text`` (nothing to relax to)."""
    txt = _has_text_label(target)
    if not txt:
        return None
    return f':has-text("{_css_str(txt)}") >> nth=-1'


def _css_str(txt: str) -> str:
    """Escape a label for embedding in a double-quoted CSS string."""
    return txt.replace("\\", "\\\\").replace('"', '\\"')


def _has_text_label(target: str) -> str | None:
    """The label inside ``target``'s first ``:has-text`` predicate, or None."""
    m = _HAS_TEXT_RE.search(target or "")
    if not m:
        return None
    return m.group(2).strip() or None


_ARIA_LABEL_RE = re.compile(r"""\[aria-label\s*=\s*(["'])(.*?)\1\s*\]""")


def _aria_label_text(target: str) -> str | None:
    """The label inside ``target``'s first ``[aria-label="X"]`` clause, or None.

    A `textbox`/`combobox` target normalises to an aria-label union with no
    `:has-text` on the icon-only path, so `_has_text_label` returns None for it;
    this recovers the accessible name the planner wrote for those verbs."""
    m = _ARIA_LABEL_RE.search(target or "")
    if not m:
        return None
    return m.group(2).strip() or None


def _aria_label_selector(target: str) -> str | None:
    """An accessible-name selector for the text in a ``:has-text`` predicate.

    ``:has-text`` matches rendered TEXT. An icon-only button has none — its
    accessible name comes from ``aria-label`` — so the predicate can never match
    it, however the tag and class are guessed. The planner cannot tell the two
    apart, because it reads the AX tree, where ``<button aria-label="Collapse
    console"><svg/></button>`` and ``<button>Collapse console</button>`` both
    render as ``button "Collapse console"``; it then writes that name into a
    ``:has-text`` predicate.

    Measured on the b0811 sweep: 667 of 1,767 ``selector not found`` steps
    carried a ``:has-text``, across 277 tasks. Both of `Dfxc2vB`'s misses were
    this exact shape (``<button id="q-pause" aria-label="Pause or resume
    (Space)">`` and ``<button id="rack-close" aria-label="Collapse console">``,
    each containing only an ``<svg>``), and ``[aria-label="X"]`` recovers both.

    Deliberately NOT ``[title="X"]``, which looks like the same idea and is not.
    Measured on that same element, ``[title=]`` matched 0: its tooltip text is
    ``Pause / resume — Space`` while its accessible name is ``Pause or resume
    (Space)``. A title attribute is prose for a human hovering; the AX name is
    what the planner actually read. Matching on the wrong one of the two would
    add a tier that fires on some other control's tooltip.

    ``nth=0`` rather than the text tier's ``nth=-1``: an ``aria-label`` names the
    element it is on, so there is no innermost/ancestor ambiguity to resolve —
    but duplicates exist in practice, and the first match must be chosen
    explicitly rather than left to Playwright's non-strict default.
    """
    txt = _has_text_label(target)
    if not txt:
        return None
    return f'[aria-label="{_css_str(txt)}"] >> nth=0'


# DEFECT 118: the planner writes an accessible NAME it read from the AX tree, but
# routinely mangles it in ways that are pure noise to a human yet fatal to an
# exact `[aria-label="X"]` / `:has-text("X")` match. Measured on b0812 (all four
# savewdb64-fix1 shards), 25 of 27 `selector not found` items were this class —
# the element was present in the AX tree under a name that differs from the
# target only by:
#   * a space rendered as an underscore -- `EMAIL_ADDRESS` (target) vs
#     `EMAIL ADDRESS` (AX). An LLM tokenises a form field's name and joins with
#     `_`; `mUCaHaj`'s sign-in email field, a CRITICAL item, gated a 1.000
#     artifact to 0.000 on this alone.
#   * a runtime counter suffix baked into the name -- `TECHNOLOGY 1` (target,
#     captured while a filter was active) vs `TECHNOLOGY` (AX at rest). `react
#     __dc28eYz`, also CRITICAL, also gated to 0.000.
#   * a composed/verbose accessible name -- `Close` (target) vs `Close dialog`
#     (AX), `Download` vs `Download ZIP`.
#
# Unlike the two tiers above (which only fire for `click`), this one must fire
# for EVERY selector verb: the two examples that zeroed a score were a `type` and
# a `click`, and the `type`/`select` verbs have no other salvage at all. It is
# safe to run for any verb because it does not manufacture a click — it returns a
# concrete selector for a REAL live element whose accessible name matches the
# planner's label under the normalisation, and the ordinary action (type into it,
# select it, click it) then runs against that element. The wrong-click failure
# mode `_note_match`'s rules guard against cannot arise here: the match is on the
# WHOLE normalised name (not token overlap), and a tie -- two live controls whose
# names normalise to the same string -- returns None, exactly like `_note_match`.
_TRAILING_COUNT_RE = re.compile(r"\s+\d+$")


def _label_norm_variants(label: str) -> list[str]:
    """Equivalence-preserving rewrites of ``label`` the planner may have mangled.

    Each variant is a form the SAME control could legitimately carry; none widens
    to a different control. Ordered most-specific first. The raw label is not
    included (the caller has already tried it verbatim and it missed)."""
    out: list[str] = []
    seen = set()

    def add(v: str) -> None:
        v = " ".join((v or "").split())
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)

    # underscore <-> space (the tokenisation artifact, both directions)
    if "_" in label:
        add(label.replace("_", " "))
    if " " in label:
        add(label.replace(" ", "_"))
    # strip a trailing runtime counter: `TECHNOLOGY 1` -> `TECHNOLOGY`
    stripped = _TRAILING_COUNT_RE.sub("", label).strip()
    if stripped and stripped != label:
        add(stripped)
        if "_" in stripped:
            add(stripped.replace("_", " "))
    return out


def _norm_key(s: str) -> str:
    """Case/space/underscore/punctuation-folded key for name equivalence."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _form_field_candidates(page: Any) -> list[dict]:
    """Visible form controls as ``{name, sel}``, keyed by accessible name.

    DEFECT 118 helper. `_note_candidates` skips these (a plain input has no click
    signal), yet `type`/`select` targets are exactly where a mangled label bites.
    The accessible name is computed the way a planner reading the AX tree sees it:
    `aria-label`, then `aria-labelledby`, then an associated/wrapping `<label>`,
    then `placeholder`. Each match is stamped with a unique attribute so the
    returned selector resolves to that one element (same idiom as
    `_note_candidates`)."""
    try:
        return _bounded_page_eval(
            page,
            r"""() => {
              const vis = el => {
                const r = el.getBoundingClientRect();
                if (r.width < 1 || r.height < 1) return false;
                const s = getComputedStyle(el);
                return s.visibility !== 'hidden' && s.display !== 'none';
              };
              const nameOf = el => {
                let n = el.getAttribute('aria-label');
                if (n && n.trim()) return n.trim();
                const lb = el.getAttribute('aria-labelledby');
                if (lb) {
                  const parts = lb.split(/\s+/).map(id => {
                    const e = document.getElementById(id);
                    return e ? e.textContent : '';
                  }).join(' ').trim();
                  if (parts) return parts;
                }
                if (el.id) {
                  const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                  if (l && l.textContent.trim()) return l.textContent.trim();
                }
                const wrap = el.closest('label');
                if (wrap && wrap.textContent.trim()) return wrap.textContent.trim();
                const ph = el.getAttribute('placeholder');
                if (ph && ph.trim()) return ph.trim();
                return '';
              };
              const out = [];
              let i = 0;
              const nodes = document.querySelectorAll('input, textarea, select');
              for (const el of nodes) {
                if (!vis(el)) continue;
                const name = nameOf(el);
                if (!name) continue;
                const attr = 'data-iv-form-cand';
                el.setAttribute(attr, String(i));
                out.push({name, sel: '[' + attr + '="' + i + '"]'});
                i++;
              }
              return out;
            }""",
            default=[],
        ) or []
    except Exception:  # noqa: BLE001
        return []


def _label_normalized_selector(page: Any, target: str) -> str | None:
    """Selector for the one live control whose accessible name equals the
    planner's label under space/underscore/counter/case normalisation, or None.

    DEFECT 118. Extracts the label the planner aimed at from ``target`` (the
    `:has-text` or `[aria-label]` clause of the already-normalised selector),
    then looks for a UNIQUE visible candidate whose accessible name matches it
    exactly (fast path) or matches one of its equivalence-preserving variants.
    A tie at the winning normalisation returns None -- never guess between two
    controls. Works for every selector verb; returns a concrete `sel` for a real
    element so no evidence is manufactured."""
    label = _has_text_label(target) or _aria_label_text(target)
    if not label:
        return None
    # `_note_candidates` covers clickable controls (buttons, links, div-row menus)
    # but deliberately NOT plain form fields — an `<input>`/`<select>`/`<textarea>`
    # carries no onclick/tabindex/pointer signal, so a `type`/`select` target
    # (`EMAIL_ADDRESS`, the exact `mUCaHaj` critical miss) would find no candidate
    # at all. Add form controls by their accessible name (aria-label, associated
    # <label>, or placeholder) so this tier serves the verbs that most need it.
    cands = list(_note_candidates(page))
    cands.extend(_form_field_candidates(page))
    if not cands:
        return None
    # Try the raw label and each variant, in order; the first normalisation that
    # yields exactly one live candidate wins. Matching is on the folded whole
    # name so `EMAIL_ADDRESS`~`EMAIL ADDRESS` and `Close`~`Close` both resolve,
    # but `Close`!~`Close dialog` does NOT collapse (different folded keys) --
    # that composed-name case is handled by the substring pass below, gated on
    # uniqueness so it cannot pick the wrong control.
    for cand_label in [label, *_label_norm_variants(label)]:
        key = _norm_key(cand_label)
        if not key:
            continue
        hits = [c["sel"] for c in cands
                if _norm_key(c.get("name") or "") == key]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None  # tie: no safe answer
    # Composed accessible name: the planner's label is a PREFIX word-set of the
    # real name (`Close` -> `Close dialog`, `Download` -> `Download ZIP`). Accept
    # only when exactly one live candidate's name starts with the label's folded
    # key followed by a word boundary -- one match means no ambiguity to resolve.
    key = _norm_key(label)
    if key:
        pref = key + " "
        hits = [c["sel"] for c in cands
                if (_norm_key(c.get("name") or "") + " ").startswith(pref)]
        if len(hits) == 1:
            return hits[0]
    return None


# DEFECT 71: the relaxed tier reports `ok` for DISPATCHING a click, not for
# clicking a CONTROL — so it can score a success on inert text.
#
# This is the only salvage tier that manufactures evidence, and the mechanism is
# structural rather than accidental. `_relaxed_text_selector` deliberately drops
# the tag and class (that is its whole purpose: the planner's guessed wrapper is
# wrong) and keeps only the LABEL, then takes `nth=-1` — the deepest match. Any
# element whose text contains the label qualifies, including a caption, a `<kbd>`
# legend, or a toast. `dispatch_event('click')` is likewise deliberate: it fires
# the listener directly so a covering canvas cannot block it. Combined, the two
# choices mean the tier cannot fail: an element with no listener absorbs the
# synthetic event silently and the step is recorded `ok` with no error.
#
# b0811 `BxrjKKx`, step 27, `button:has-text("menu")` (0 matches — the real
# control is `<button id="burger" aria-label="Open station menu">`, an icon-only
# button with no text). The relaxed selector `:has-text("menu") >> nth=-1`
# matched one element:
#
#   <span id="toastMsg">Use ← → or the bottom bar · press M for menu</span>
#
# the transient hint toast, whose computed `pointer-events` is `none`. A real
# user cannot click it; `dispatch_event` does not care. The step reported `ok`,
# the drawer stayed shut (`menu.is-on=False`, `#menuList` parked at x=-405 by
# `.menu{transform:translateX(-102%)}`), and steps 29/35/41 then failed with
# `element is outside of the viewport` — which reads, to anyone auditing the
# trace, as a broken side menu. The menu was fine. We never opened it.
#
# That cascade is the reason this defect matters more than its own step count:
# a false `ok` mid-trajectory is not one lost step, it is a wrong PREMISE for
# every step after it. Census over the b0811 `full` baseline: the tier fired 866
# times across 466 tasks (0.70% of 123,192 steps); 41 fires (4.7%) produced an
# empty `ax_diff` — a click that changed nothing observable — and 78 (9.0%) were
# followed within five steps by a visibility/viewport failure.
#
# The gate is ACTUATABILITY, deliberately weaker than `_INTERACTIVE_JS`. That
# constant tests for a control by tag/role/tabindex, and using it here would
# break the idiom this tier exists to serve: DEFECT 60's div-row game menus have
# no interactive tag, role, or tabindex at all — only a JS listener and
# `cursor:pointer`. So the test is instead "could a user actuate this?":
#
#   1. `pointer-events: none` — disqualifying, and on its own enough to reject
#      the toast. A user's click passes straight through; only a synthetic event
#      reaches it. This is the clause that separates decoration from control.
#   2. laid out — zero-area elements are not clickable surfaces.
#   3. some affordance — an interactive tag/role, a non-negative `tabindex`, an
#      inline handler, `cursor:pointer`, or a `data-*` hook of the kind row
#      menus use (`data-goto`, `data-i`). `cursor:pointer` is what makes the
#      div-row menus pass.
#
# Verified on the real artifact: `.mitem` station rows (`<button role="tab">`,
# `pointer-events:auto`) pass; `#toastMsg` (`pointer-events:none`) fails; and
# `:has-text("menu")` has ZERO actuatable matches, so the tier now declines and
# hands the step to the note matcher — which resolves `#burger` and opens the
# drawer. Declining is the point: a missed click leaves an item indeterminate
# and recoverable, while a false success is a confident wrong verdict.
_ACTUATABLE_JS = """el => {
  const s = getComputedStyle(el);
  if (s.pointerEvents === 'none') return false;
  if (s.visibility === 'hidden' || s.display === 'none') return false;
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return false;
  const t = el.tagName;
  if (['BUTTON','A','INPUT','SELECT','TEXTAREA','SUMMARY','OPTION','LABEL']
      .includes(t)) return t !== 'A' || el.hasAttribute('href');
  const role = (el.getAttribute('role') || '').toLowerCase();
  if (['button','link','tab','checkbox','radio','switch','menuitem','option',
       'slider','spinbutton','combobox','textbox'].includes(role)) return true;
  if (el.isContentEditable) return true;
  const ti = el.getAttribute('tabindex');
  if (ti !== null && !ti.trim().startsWith('-')) return true;
  if (el.onclick) return true;
  if (s.cursor === 'pointer') return true;
  for (const a of el.attributes)
    if (/^data-(goto|i|idx|index|action|act|target|nav|to|step|tab|key)$/
        .test(a.name)) return true;
  return false;
}"""


def _ax_name_selector(target: str) -> str | None:
    """Playwright ``role=`` selector for the text in a ``:has-text`` predicate.

    DEFECT 113. `:has-text` matches ``textContent``; the accessible name follows
    ``innerText``, and the two use DIFFERENT joins for an element built from
    child spans. For the idiom every one of these artifacts uses for a nav
    button --

        <button class="nav-btn"><span class="ico">&#9670;</span><span>Store</span></button>

    -- Chrome's accname algorithm concatenates the children with a SPACE and
    reports ``&#9670; Store``, which is what the planner reads out of the AX tree
    and writes into its target. ``textContent`` concatenates with NOTHING and
    yields ``&#9670;Store``. So the planner's string is, by construction, a
    string no element's text contains, and no amount of tag/class relaxation can
    find it: measured on `F7wmUxV`, `:has-text("&#9670; Store")` matched 0 and
    the relaxed text tier matched 0.

    Playwright's ``role=button[name="..."]`` engine is backed by that same
    accname algorithm, so it matches the element the planner actually saw. On
    that page it returned exactly 1 for each of three failing labels.

    It is also the SAFER match, not merely a wider one. For ``&#9738; Daily`` the
    relaxed text tier did find something -- ``<div class="toast gold">``, a
    transient hint banner with ``pointer-events: none`` -- while the role engine
    picked ``<button id="navDaily">``. That is DEFECT 71's failure mode
    (a `dispatch_event` on inert text reporting `ok`) avoided by construction:
    a role match is a control by definition.

    ``name=`` is an EXACT, case-insensitive, whitespace-normalized match in
    Playwright, which is what we want here -- the planner is quoting a name it
    read verbatim, so a substring match would only add ambiguity. Returns None
    when the target carries no ``:has-text`` (nothing to read a name from) or no
    recognisable role, because a guess with no evidence behind it is worse than
    an honest miss.
    """
    txt = _has_text_label(target)
    if not txt:
        return None
    role = _selector_role(target)
    if not role:
        return None
    return f'role={role}[name="{_css_str(txt)}"]'


# The ARIA role implied by a selector we generated from an AX phrase. Keyed off
# the CSS `normalize_selector` emits, so the two stay in step: `button 'X'`
# becomes `:is(button, [role=button]):has-text("X")`, and this reads `button`
# back out of it.
_SELECTOR_ROLE_HINTS = (
    ("[role=button]", "button"), ("[role=link]", "link"),
    ("[role=tab]", "tab"), ("[role=option]", "option"),
    ("[role=checkbox]", "checkbox"), ("[role=radio]", "radio"),
    ("[role=switch]", "switch"), ("[role=slider]", "slider"),
    ("[role=combobox]", "combobox"),
)


def _selector_role(target: str) -> str | None:
    """The ARIA role ``target`` is asking for, or None if it does not say.

    Reads the `[role=...]` alternative `normalize_selector` writes rather than
    guessing from the tag, so a planner-authored `button:has-text("X")` (which
    never went through normalization and carries no `[role=]`) is still handled
    by the bare-tag fallback below.
    """
    t = (target or "").lower()
    for needle, role in _SELECTOR_ROLE_HINTS:
        if needle in t:
            return role
    # A plain `button:has-text(...)`/`a:has-text(...)` the planner wrote itself.
    m = re.match(r'^\s*(button|a)\s*[:\[]', t)
    if m:
        return "button" if m.group(1) == "button" else "link"
    return None


def _try_relaxed_text_click(page: Any, target: str) -> bool:
    """Native-dispatch a click on the innermost element matching the text of
    ``target``'s ``:has-text`` predicate. Returns True if a click was dispatched.

    Uses ``dispatch_event('click')`` rather than a coordinate click so it (a)
    fires the element's own click listener directly — the div-row menus have no
    hit-test target a coordinate click could rely on being on top — and (b) is
    immune to a full-screen canvas/overlay stacked above the row. Best-effort:
    any failure (no ``:has-text``, no match, stub page) returns False and the
    caller falls back to reporting the original selector miss.

    Three tiers, and none is a variation on another. `:has-text` matches rendered
    TEXT; an icon-only button has none, and its accessible name comes from
    `aria-label`. The planner cannot tell the difference, because it reads the AX
    tree — where an `aria-label` and a text node produce the same
    `button "Collapse console"` line — and then writes the name into a `:has-text`
    predicate that can never match. Measured on b0811: 667 of 1,767
    `selector not found` steps carried a `:has-text`, across 277 tasks. On
    `Dfxc2vB` both misses were this exact shape (`<button id="q-pause"
    aria-label="Pause or resume (Space)">` and `<button id="rack-close"
    aria-label="Collapse console">`, each containing only an `<svg>`), and
    `[aria-label="..."]` recovers both.

    The third tier is a distinct mechanism again: a name ASSEMBLED from child
    spans belongs to no single element's text, so neither of the other two can
    reach it. See `_ax_name_selector` for the measurement.

    Ordered ROLE-first, then text, then aria-label. The role tier leads because
    it is the only one that matches on the same computation the planner read (the
    accessible name) and the only one that cannot land on inert text: DEFECT 113
    measured the text tier picking a `pointer-events:none` toast for a label
    whose real control the role engine found. Between the remaining two, text
    before aria-label: an `aria-label` may sit on a wrapper while the visible row
    is the thing to click, whereas a text match lands on the element the label
    was read off.

    DEFECT 71 gates every tier on `_ACTUATABLE_JS`. See that constant for why a
    tier that reports success for *dispatching* a click, rather than for
    clicking a control, is the one salvage path that can manufacture evidence.
    """
    for rel in (_ax_name_selector(target),
                _relaxed_text_selector(target),
                _aria_label_selector(target)):

        if not rel:
            continue
        try:
            _hits = _bounded_query_all(page, rel)  # bounded (see _bounded_query_all)
            el = _hits[0] if _hits else None
            if el is None:
                continue
            if not el.evaluate(_ACTUATABLE_JS):
                continue
            page.dispatch_event(rel, "click", timeout=ACTION_TIMEOUT_MS)
            return True
        except Exception:
            continue
    return False


# DEFECT 60: the salvage tiers above BOTH gate on a `:has-text` predicate, so
# neither fires for a selector the planner simply invented. Censused over the
# v011 run: 5,654 `selector not found` steps, of which 4,950 (88%) carry a
# `:has-text` and are already salvageable -- and **704 steps across 357 tasks
# (12%) carry none**, so no tier fires at all. `BxrjKKx` critical item 11 is
# this shape: the planner wrote `button.menu-btn`, the real control is
# `<button class="burger" id="burger" aria-label="Open station menu">`, the menu
# never opened, and the item could not pass.
#
# 703 of those 704 steps carry a human-readable `note` ("Open the side menu").
# That looks like an obvious signal, and probing it on the real page showed it
# is a TRAP if used naively -- token overlap with a standard stopword list
# scored `#burger` ("Open station menu") and `#menuClose` ("Close menu") EQUAL,
# because the only discriminating token, the verb, is a stopword. Clicking the
# opposite control is far worse than not clicking: a missing selector degrades
# the item to indeterminate, while a wrong click produces a confident wrong
# verdict. So the rules below are built to return NOTHING rather than guess.
#
# Directional verbs, kept out of the stopword list and used as a disqualifier.
_ANTONYMS = {
    "open": "close", "close": "open", "show": "hide", "hide": "show",
    "start": "stop", "stop": "start", "play": "pause", "pause": "play",
    "next": "prev", "prev": "next", "expand": "collapse",
    "collapse": "expand", "add": "remove", "remove": "add",
    "increase": "decrease", "decrease": "increase", "enable": "disable",
    "disable": "enable", "in": "out", "out": "in", "up": "down", "down": "up",
}

# Deliberately does NOT contain the antonym verbs above.
_NOTE_STOP = {
    "the", "a", "an", "to", "in", "of", "and", "or", "for", "via", "with",
    "then", "it", "this", "that", "is", "at", "by", "on", "button", "click",
    "again", "its", "from", "into", "onto",
}


# A target that looks like this is a SELECTOR the page no longer has, not a
# label. Tokenizing `#hud .btn[data-id="3"]` would strip the punctuation and
# leave `hud btn data` -- three fine-looking content words that mean nothing to
# a human reader and would score against arbitrary controls. A target only
# feeds the note matcher when it carries none of this syntax.
_CSS_ISH = re.compile(r'[#.\[\]>:()=]|^(?:div|button|input|canvas|span|a|select'
                      r'|textarea|body|html|svg|li|ul|section|header|nav)\b',
                      re.I)


def _note_prose(text: str) -> str:
    """A note with the pipeline's own bracketed annotations removed.

    The planner and executor append machine tags to `note` -- `[auto:
    directional probe]`, `[normalized selector]`, `[pinned union to matching
    branch]`, `[relaxed :has-text click]`. They are provenance, not a
    description of the control, and `_note_tokens` cannot tell the difference:
    it read `auto`, `directional`, `probe` as three perfectly good content
    words.

    That is not a theoretical concern. On `BxrjKKx` the planner emitted six
    steps -- `angle slider`, `acid volume slider`, `temperature slider`, `CO2
    slider`, `pause button`, `reset button` -- whose entire note was
    ` [auto: directional probe]`. Six DIFFERENT targets carrying one IDENTICAL
    tag, so the matcher resolved all six to the same control and marked each
    step `ok`. Six items got a confident verdict from a click that never
    touched the control they were about: exactly the wrong-click failure the
    four rules exist to prevent, arriving through a door the rules could not
    see, because a machine tag is not prose and none of the rules test for
    prose.
    """
    return re.sub(r"\[[^\]]*\]", " ", text or "")


def _note_tokens(text: str) -> set[str]:
    """Content words of a note or an accessible name, lowercased.

    Tokens shorter than 3 chars are dropped EXCEPT the directional ones, which
    are the whole point -- `up`/`in` are two characters and are exactly the
    words that separate a control from its opposite.
    """
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in _NOTE_STOP:
            continue
        if len(w) > 2 or w in _ANTONYMS:
            out.add(w)
    return out


def _note_candidates(page: Any) -> list[dict]:
    """Visible, clickable elements with an accessible name, as
    ``{name, sel}``. Visibility is part of the query, not a later filter:
    the probe found three candidates scoring a perfect overlap for
    "Close the theory corner" that were all `display:none`.

    The query does NOT stop at semantic roles. These artifacts are hand-written
    and the most-targeted control on `BxrjKKx` is
    `<div class="menu__item"><span>11</span><span>Bacterial Growth Incubator</span></div>`
    -- a bare div with a click handler and no role, no aria-label, no tabindex.
    A role-only query declines every one of them, which is most of what the
    planner points at in a side menu. So an element also qualifies on any of the
    three signals a hand-written control actually carries: an inline `onclick`,
    a `tabindex`, or a computed `cursor: pointer`.

    `cursor` INHERITS, so a pointer div makes every descendant a candidate too.
    That is handled by dropping any candidate containing another candidate: the
    innermost one wins, and a click on it bubbles to whichever ancestor holds the
    handler. Without that suppression a wrapper and its label tie on identical
    text and rule 3 would throw the match away.
    """
    try:
        return _bounded_page_eval(
            page,
            """() => {
              const q = 'button,a,[role=button],[role=tab],[role=menuitem],'
                      + 'input[type=button],input[type=submit],summary,'
                      + '[onclick],[tabindex]';
              const hit = [];
              const all = document.querySelectorAll('*');
              for (let i = 0; i < all.length && hit.length < 400; i++) {
                const e = all[i];
                const st = getComputedStyle(e);
                if (!(e.matches(q) || st.cursor === 'pointer')) continue;
                const r = e.getBoundingClientRect();
                if (!(r.width > 0 && r.height > 0)) continue;
                if (st.visibility === 'hidden' || st.display === 'none') continue;
                if (parseFloat(st.opacity || '1') < 0.05) continue;
                const name = (e.getAttribute('aria-label')
                              || e.getAttribute('title')
                              || e.textContent || '').trim().slice(0, 80);
                if (!name) continue;
                hit.push(e);
              }
              // innermost wins: a pointer ancestor is not the control, its leaf is
              const out = [];
              hit.forEach((e, i) => {
                if (hit.some(o => o !== e && e.contains(o))) return;
                const name = (e.getAttribute('aria-label')
                              || e.getAttribute('title')
                              || e.textContent || '').trim().slice(0, 80);
                e.setAttribute('data-iv-note-cand', String(i));
                out.push({name: name, sel: '[data-iv-note-cand="' + i + '"]'});
              });
              return out;
            }""",
            default=[])
    except Exception:  # noqa: BLE001 - a stub page or a hostile CSP is not an error
        return []


def _note_match(page: Any, note: str) -> str | None:
    """Selector for the one visible control whose accessible name matches
    ``note``, or None when the answer is not unambiguous (DEFECT 60).

    Four rules, each of which exists because the probe showed its absence
    produces a WRONG click rather than merely a missed one:

    1. **noun floor** -- a match must share at least one non-directional word.
       "Close the theory corner" vs "Close menu" overlaps only on `close`;
       without this rule that scores as a match and clicks the wrong control.
    2. **antonym disqualifier** -- a candidate naming the opposite direction is
       removed outright, not merely down-ranked. "Open the side menu" must never
       resolve to "Close menu".
    3. **uniqueness** -- a tie at the top score returns None. The tie is the
       exact failure mode this defect is about; there is no safe way to break it.
    4. **visibility** -- enforced in `_note_candidates`.

    Measured on the real `BxrjKKx` page: 6 of 7 notes resolve correctly and the
    7th ("Close the theory corner", whose target is genuinely not visible)
    returns None instead of clicking `#menuClose`.
    """
    prose = _note_prose(note)
    # An EXACT whole-name match precedes the token rules and is exempt from the
    # two-word floor below. The floor exists because one shared word cannot
    # discriminate between two different targets; an exact match cannot have
    # that failure, since two different targets cannot both equal the same
    # string. Without this exemption the tier declines every single-word button
    # label -- `RESUME`, `ROLL`, `Unlock`, `CLEAR`, `Serve` -- which is what the
    # census found 29% of unsalvageable targets actually are. Still subject to
    # rule 3: two controls sharing a label is a tie, and a tie is no answer.
    want = " ".join((prose or "").lower().split())
    if want:
        exact = [c["sel"] for c in _note_candidates(page)
                 if " ".join((c.get("name") or "").lower().split()) == want]
        if len(exact) == 1:
            return exact[0]
        if exact:
            return None
    nt = _note_tokens(prose)
    # rule 5: the note must be PROSE, and enough of it to be evidence. Bracketed
    # machine tags are stripped first (see `_note_prose`), and what remains must
    # carry at least two content words. One word cannot discriminate: `BxrjKKx`
    # had six differently-targeted steps whose whole note was a single shared
    # tag, and every one of them resolved to the same control and was recorded
    # as a success. Two words is not a strong bar, but it is the difference
    # between a description and a label.
    if len(nt) < 2:
        return None
    scored: list[tuple[int, str]] = []
    for c in _note_candidates(page):
        ct = _note_tokens(c.get("name") or "")
        if not ct:
            continue
        # rule 2: naming the opposite direction disqualifies
        if any(_ANTONYMS.get(w) in ct for w in nt):
            continue
        shared = nt & ct
        # rule 1: a verb-only overlap is not evidence
        if not (shared - set(_ANTONYMS)):
            continue
        scored.append((len(shared), c["sel"]))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    # rule 3: an ambiguous winner is no winner
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


# DEFECT 72: a note that states its PURPOSE ties on the purpose, not the control.
#
# `_note_match`'s rule 3 (a tie is no answer) is right, and it is why the tier is
# safe. But a planner note is not a label — it is a sentence, and the sentences
# it writes routinely name TWO things: the control to actuate, and the reason for
# actuating it. "Open side menu to navigate to Earth Science" names the burger
# button and the destination. The destination's words then match every element
# that mentions it, and because the control contributes only two tokens
# (`open`, `menu`), the true target ties with the scenery:
#
#   note "Open side menu to navigate to Earth Science"   (BxrjKKx step 33)
#     2  "Open station menu"              <- the control (#burger)
#     2  "Earth Science"                  <- a station row
#     2  "Earth Science Challenge Lab"    <- another station row
#     2  "Station 17: Earth Science Challenge Lab"
#
#   note "Open side menu to navigate to a Challenge Lab" (BxrjKKx step 39)
#     2  "Open station menu"              <- the control
#     2  "Physics Challenge Lab" / "Chemistry Challenge Lab" / "Biology
#        Challenge Lab" / "Challenge Lab · 3 tasks" x2   <- five ties
#
# Rule 3 fires and the tier declines — correctly, on the evidence it was given.
# The evidence was the problem: the note's second clause describes a place, not a
# thing to click. This is why `BxrjKKx` had three consecutive menu-navigation
# steps fail while the SAME control resolved fine for a fourth ("Open side menu
# to navigate to Biology" — where `biology` happens not to tie).
#
# The remedy is to ask the same question of a shorter note. English puts the verb
# and its object first and the purpose in a trailing adjunct, so truncating at
# the first purpose marker keeps the control clause and drops the destination.
# Retried only when the full note TIED: a tie means the note carried more
# subjects than one control can account for, which is exactly the signal that a
# trailing clause is in play. A note that resolved uniquely is never re-read, and
# a note with no purpose marker is unchanged — so this can only convert a None
# into an answer, never move an answer.
#
# Safety is inherited whole: the truncated note runs through the SAME four rules.
# The noun floor still demands a shared content word, the antonym disqualifier
# still bars "Close menu", and rule 3 still returns None if the head clause is
# itself ambiguous. Verified on the real page: all three previously-declining
# notes resolve to `#burger`, and clicking it opens the drawer (`menu.is-on` goes
# True), which is what steps 29/35/41 needed.
#
# Ordered longest-first so the most specific truncation is tried first: " to "
# before " so ", and " for " last, because `for` also appears inside control
# names ("Search for a station") where truncating would discard the object.
_PURPOSE_MARKERS = (" to navigate to ", " in order to ", " so that ",
                    " to reveal ", " to open ", " to view ", " to show ",
                    " to check ", " to see ", " to ", " so ", " then ",
                    " and then ", " for ")


def _note_head_clause(note: str) -> str | None:
    """``note`` truncated at its first purpose marker, or None if unchanged.

    Returns None when there is no marker, when the marker is at position 0, or
    when the head clause is too short to carry the two content words
    `_note_match`'s rule 5 requires — in each case there is nothing to retry.
    """
    prose = " ".join(_note_prose(note).split())
    if not prose:
        return None
    low = prose.lower()
    cut = None
    for mark in _PURPOSE_MARKERS:
        i = low.find(mark)
        if i > 0 and (cut is None or i < cut):
            cut = i
    if cut is None:
        return None
    head = prose[:cut].strip()
    if not head or head == prose or len(_note_tokens(head)) < 2:
        return None
    return head


def _note_match_relaxed(page: Any, note: str) -> str | None:
    """`_note_match`, retried on the note's head clause when the full note ties.

    DEFECT 72. The retry runs the identical four rules against a shorter note,
    so it can only turn a decline into an answer; it never overrides one.
    """
    sel = _note_match(page, note)
    if sel:
        return sel
    head = _note_head_clause(note)
    return _note_match(page, head) if head else None


DESKTOP_VP = (1280, 720)
MOBILE_VP = (390, 844)


def capture_viewport_shot(page: Any, frames_dir: Path, name: str, *,
                          width: int, height: int) -> str | None:
    """Set the viewport, settle, full-page screenshot into frames_dir/name.
    Best-effort: returns the basename on success, None on any failure.

    Bounded — every Playwright sync call here (`set_viewport_size`,
    `page.screenshot(full_page=True)`) is unbounded and dead-waits on a pegged
    renderer; b0811 `miniapp_interactive-art_generati__a2YwBBP` wedged the
    trajectory finalize at exactly `page.set_viewport_size`. Each call runs on a
    daemon thread with a short join; on timeout we skip the shot and return
    None (best-effort is the docstring contract)."""
    box: dict[str, Any] = {}

    def _resize() -> None:
        try:
            page.set_viewport_size({"width": width, "height": height})
            box["ok"] = True
        except Exception:  # noqa: BLE001
            box["ok"] = False

    th = threading.Thread(target=_resize, name="viewport-resize", daemon=True)
    th.start()
    th.join(3.0)
    if th.is_alive() or not box.get("ok"):
        return None
    settle(page, 600)
    dest = Path(frames_dir) / name

    def _shot(full: bool) -> None:
        try:
            if full:
                page.screenshot(path=str(dest), full_page=True)
            else:
                page.screenshot(path=str(dest))
            box["shot_ok"] = True
        except Exception:  # noqa: BLE001
            box["shot_ok"] = False

    box["shot_ok"] = False
    th = threading.Thread(target=lambda: _shot(True), name="viewport-shot", daemon=True)
    th.start()
    th.join(10.0)
    if th.is_alive() or not box.get("shot_ok"):
        # retry without full_page for stub pages that ignore the kwarg
        box["shot_ok"] = False
        th = threading.Thread(target=lambda: _shot(False), name="viewport-shot-plain",
                              daemon=True)
        th.start()
        th.join(10.0)
        if th.is_alive() or not box.get("shot_ok"):
            return None
    return name if dest.exists() else None


_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _slug(value: Any, fallback: str = "na") -> str:
    """Filename-safe form of a `for_item` stamp.

    The model may label an item "item 2" / "#2", which would otherwise land in a
    frame filename verbatim (`012_item 2_before.png`) — spaces and punctuation in
    evidence filenames break shell/URL handling downstream."""
    s = _SLUG_RE.sub("_", str(value)).strip("_")
    return s or fallback


def _frame_is_blank(path: Any) -> bool | None:
    """Does this screenshot show a single flat colour? None when undecidable.

    The last renderability channel that does NOT need the renderer's JS main
    thread. Screenshots come off CDP's compositor, so a page pegged in a rAF
    loop still paints — measured on the probe set: a pegged canvas game returned
    an empty `dom_snapshot` and a 3-node AX tree while its frame was 52% top
    colour (i.e. plainly drawn). Truly empty pages sit at 1.0000.

    The threshold is 0.995 rather than 1.0 because a real blank page is not
    bit-uniform: `ok.html` (an h1 and a button on default white) measured 0.9983
    top-colour, so anything at or below that is content. A blank page with a
    background-colour rule still measures 1.0.
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - Pillow absent: this channel just abstains
        return None
    if not path or not os.path.exists(str(path)):
        return None
    try:
        im = Image.open(str(path)).convert("RGB")
        colors = im.getcolors(maxcolors=1 << 20)
    except Exception:  # noqa: BLE001 - unreadable/huge frame: abstain, never raise
        return None
    if not colors:
        return False       # too many distinct colours to enumerate => painted
    total = sum(n for n, _ in colors)
    if not total:
        return None
    return (max(n for n, _ in colors) / total) > 0.995


def _is_renderable(init: dict[str, Any], page_errors: list[Any]) -> bool:
    """Did the artifact actually put something on screen?

    This gates the ENTIRE score to 0.0, so it has to answer that question and no
    other. The previous rule — non-empty `dom_snapshot` AND zero page errors —
    answered two different ones, and both halves misfired on the b0811 sweep,
    where 216 of 579 verdicts (37%) were gated "artifact does not render":

    * An empty `dom_snapshot` does not mean an empty page. Both DOM reads in
      `observe` are bounded evals that need the renderer's main thread, so an
      artifact running a busy rAF/WebGL loop returns "" for a page that is
      painting fine. This was 190 of the 216, and 160 of them had a painted
      initial frame. An actually-blank page returns 55 chars of skeleton
      outerHTML, not "" — emptiness here means saturation, not blankness.
    * A single uncaught JS error is not non-renderability. `$ is not defined`
      from a stray jQuery call gated a deep-sea RPG that drew its whole
      character-select screen; the human scored that same artifact 2 of 7 items
      passing, not 0. Page errors remain in the judge's evidence (see
      `scorer._environment_lines`), where a per-item judgment can weigh them —
      which is the right place for a signal that is usually partial.

    So: renderable if either main-thread-independent channel shows content. The
    AX tree comes over CDP and the screenshot off the compositor; only the DOM
    read needs JS. Requiring more than 1 AX node skips the lone `RootWebArea`
    that a blank document still reports.

    `dom_snapshot` is deliberately NOT a positive signal here, even though it is
    the judge's richest evidence. `observe` falls back to `documentElement
    .outerHTML` when `innerText` is empty, so an empty document still yields ~55
    chars of `<html><head>…` skeleton — non-empty for a page with nothing on it.
    It only serves as the last resort when both other channels abstain (no CDP,
    no Pillow), where matching the old lenient behaviour is the safe direction:
    this gate zeroes a whole artifact, so an undecidable case must not gate.
    """
    if len(init.get("ax") or []) > 1:
        return True
    blank = _frame_is_blank(init.get("screenshot"))
    if blank is not None:
        return not blank
    return bool((init.get("dom_snapshot") or "").strip())


def _burst_shot(page: Any, dest: Path) -> bool:
    """One cheap screenshot straight to `dest`. True on success.

    Not `observe()`: that also reads innerText, outerHTML and the AX tree, all of
    which need the renderer's main thread and cost up to OBSERVE_TIMEOUT_MS each.
    A burst frame wants only pixels, which come off CDP's compositor and are
    still produced by a page pegged in a rAF loop — the exact page whose motion
    we most need to measure.
    """
    try:
        page.screenshot(path=str(dest), timeout=BURST_SHOT_TIMEOUT_MS)
    except TypeError:
        # Stub pages in the smokes accept screenshot(path=...) only.
        try:
            page.screenshot(path=str(dest))
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001 - saturated renderer; abandon the burst
        return False
    try:
        return dest.exists() and dest.stat().st_size > 0
    except OSError:
        return False


def _capture_burst(page: Any, frames_dir: Path, stem: str, dwell_ms: int,
                   n_frames: int = BURST_FRAMES) -> list[str]:
    """Sample the dwell window, returning the frame names in time order.

    DEFECT 67. The dwell exists because effects are not instantaneous, and until
    now it was slept whole and then photographed once. That yields a pair, and a
    pair can only answer "did the endpoints differ" — which is the wrong question
    for "is it still animating", "did Pause stop it", and "does the camera
    orbit". Those need to know whether the frames stop differing from each other.

    The frames are spread over the SAME window, so the last burst frame lands at
    the end of the dwell exactly as the old single observation did: the sleep is
    divided into `n_frames` slices rather than extended. The only added wall-clock
    is the screenshots (~50ms each off the compositor).

    Returns [] when nothing could be captured, which callers must treat as no
    evidence rather than as no motion (DEGRADE-NEVER-GATE).
    """
    if dwell_ms <= 0 or n_frames <= 0:
        return []
    # Never sample faster than the floor: frames 40ms apart differ by one
    # animation tick on every artifact, so "still moving" would be the answer
    # everywhere and the pause signal would be destroyed.
    n = max(1, min(int(n_frames), max(1, dwell_ms // max(1, BURST_MIN_GAP_MS))))
    gap = max(BURST_MIN_GAP_MS, dwell_ms // n)
    names: list[str] = []
    for i in range(n):
        try:
            page.wait_for_timeout(gap)
        except Exception:  # noqa: BLE001 - stub page
            pass
        name = f"{stem}_burst{i + 1}.png"
        if not _burst_shot(page, Path(frames_dir) / name):
            # A page that cannot answer a compositor screenshot in 2s is
            # saturated; the rest of this burst would time the scheduler, not
            # the artifact. Sleep out the remainder so the AFTER frame still
            # lands where the plan asked for it.
            rest = gap * (n - i - 1)
            if rest > 0:
                try:
                    page.wait_for_timeout(rest)
                except Exception:  # noqa: BLE001
                    pass
            break
        names.append(name)
    return names


def _persist(src: str | None, frames_dir: Path, name: str) -> str | None:
    if not src or not os.path.exists(src):
        return None
    import shutil
    dest = frames_dir / name
    try:
        shutil.copyfile(src, dest)
        return name
    except OSError:
        return None


def run_trajectory(page: Any, actions: list[dict[str, Any]], entry_url: str, *,
                   out_dir: Path, max_steps: int = 40, step_timeout_s: int = 15,
                   needs_mobile: bool = False,
                   exec_budget_s: float = EXEC_BUDGET_S) -> dict[str, Any]:
    frames_dir = Path(out_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    console_errors: list[Any] = []
    page_errors: list[Any] = []
    failed_requests: list[dict[str, str]] = []
    bad_responses: list[dict[str, Any]] = []

    def _on_request_failed(req: Any) -> None:
        # Normalise a Playwright Request into {url, failure}. In the Python sync
        # API `request.url` is a str property and `request.failure` is a str (the
        # error text) or None — but we tolerate callables/dicts too so this is
        # robust to API drift and to fake pages in tests. Never raises.
        try:
            url = req.url() if callable(getattr(req, "url", None)) else getattr(req, "url", "")
            fail = req.failure() if callable(getattr(req, "failure", None)) else getattr(req, "failure", None)
            if isinstance(fail, dict):
                fail = fail.get("errorText")
            failed_requests.append({"url": str(url or ""), "failure": str(fail or "")})
        except Exception:
            pass

    def _on_response(resp: Any) -> None:
        """Record error-status responses (4xx/5xx) with their resource type.

        Same defensive shape as `_on_request_failed`: every field is read through
        both the property and the callable form so a fake page in the smoke test
        works, and nothing here may raise into Playwright's event loop. We keep
        only error statuses — a page loads dozens of successful responses and
        holding them all would be a slow per-artifact memory leak."""
        try:
            status = resp.status() if callable(getattr(resp, "status", None)) else getattr(resp, "status", 0)
            status = int(status or 0)
            if status < 400:
                return
            url = resp.url() if callable(getattr(resp, "url", None)) else getattr(resp, "url", "")
            req = getattr(resp, "request", None)
            req = req() if callable(req) else req
            rtype = ""
            if req is not None:
                rt = getattr(req, "resource_type", "")
                rtype = rt() if callable(rt) else rt
            if len(bad_responses) < 200:   # bound: a pathological page can 404 in a loop
                bad_responses.append({"url": str(url or ""), "status": status,
                                      "resource_type": str(rtype or "")})
        except Exception:
            pass

    try:
        page.on("console", lambda m: console_errors.append(getattr(m, "text", str(m)))
                 if getattr(m, "type", "") == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        # `requestfailed` fires for a resource that never loaded (DNS failure,
        # blocked, connection refused) — the signal for the CDN-unreachable
        # build defect. detect_cdn_defects filters to genuinely cross-origin misses.
        page.on("requestfailed", _on_request_failed)
        # A 404 is a COMPLETED response, so it does NOT fire `requestfailed` —
        # `response` is the only place a missing-but-served-an-error asset is
        # visible. This is the signal for CLASS 3 (missing-own-asset): the
        # artifact's own script 404s, it paints its author-written "failed to
        # load" sheet, and every check fails accurately for a reason that is
        # about the bundle, not the code.
        page.on("response", _on_response)
    except Exception:
        pass  # fake pages / no event support

    # Arm the frame counter BEFORE navigating — `add_init_script` applies to the
    # next document, and everything from the first paint through settle, the first
    # observe and the build-defect sweep is then time the counter accumulates for
    # free. By the read at `action_timeout_ms` below it has averaged over several
    # seconds of the page's real behaviour, which is both cheaper and a better
    # estimate than any window we could sample on demand. See `_FPS_INSTALL_JS`.
    install_fps_counter(page)
    navigate(page, entry_url)
    settle(page)

    # DEFECT 43 — lift a full-screen entry gate BEFORE any evidence is taken.
    # Position matters: run this after `observe()` and the initial frame, the AX
    # seed, the `renderable` decision and the overlay sweep would all describe the
    # gate instead of the artifact — and every later step's `ax_diff` would be
    # measured against a page the user never sees. Doing it here costs one probe
    # on an ungated page (the routine no-ops when nothing covers the centre) and
    # buys back every check behind a legitimate "click to begin" sheet.
    #
    # A modal at LOAD time is an onboarding/gesture gate, never a check target:
    # no checklist item can be about a dialog that was already open before the
    # first action. That is what makes dismissing unconditionally safe here and
    # would not be safe mid-run.
    gate_defects = dismiss_entry_gate(page)

    frames: list[str] = []
    init = observe(page)
    init_name = _persist(init.get("screenshot"), frames_dir, "000_initial.png")
    if init_name:
        frames.append(init_name)
    renderable = _is_renderable(init, page_errors)
    # `renderable` was decided from ONE frame taken at `settle()`. That is too
    # early for an artifact whose first paint waits on an async asset, a shader
    # compile, or a boot overlay's own timer: b0811 `CwjyUPc` had an empty AX
    # tree and a blank 000_initial.png, was gated to 0.0 as non-renderable, and
    # painted from frame 001 onward — the reviewer opened it by hand and saw it
    # immediately. Since this one flag zeroes an entire artifact, a later frame
    # that demonstrably painted must be allowed to overturn it. The re-check runs
    # after the action loop (see below), where the frames exist.
    # Running AX state, so every step can report what IT changed. Seeded from the
    # load-time tree: step 1's diff is against the page as it first appeared.
    prev_ax: list = init.get("ax") or []

    # Build-defect detection (annotate-only): a page can "render" yet be dead —
    # a forced-visible fallback overlay covering the game, or an unreachable CDN
    # dependency. We detect both at load time and attach them to the trajectory;
    # the functional score is left untouched (pipeline copies them to the
    # verdict for cleaner training signal). Best-effort — never raises.
    build_defects = detect_overlay_defects(page)
    build_defects += gate_defects
    build_defects += detect_cdn_defects(failed_requests, entry_url)
    build_defects += detect_missing_asset_defects(bad_responses, entry_url)
    # Hosts already flagged at load time, so the end-of-run sweep below reports
    # each unreachable dependency once rather than twice.
    _cdn_hosts_at_load = {d.get("detail") for d in build_defects
                          if d.get("kind") == "cdn-unreachable"}
    # Same, for CLASS 3. A same-origin script usually 404s during load, so this
    # set is normally the whole answer — but a dynamically injected <script> can
    # 404 mid-run, which is exactly why the sweep repeats at the end.
    _missing_at_load = {d.get("detail") for d in build_defects
                        if d.get("kind") == "missing-own-asset"}

    steps: list[dict[str, Any]] = []
    idx = 0
    # This artifact's own click budget, read once from the counter installed at
    # navigate. Once per trajectory rather than per step: a page's paint rate is
    # a property of what it renders, not of which step is running. Read HERE
    # rather than at install time so the counter has spanned load, settle, the
    # first observe and the build-defect sweep — several seconds of the page's
    # actual behaviour, not a 1.2s window whose own cost is paid at the frame
    # period it is trying to measure (v0.9 defect 29).
    action_timeout_ms = _frame_scaled_timeout_ms(page)
    _deadline = time.monotonic() + max(0.0, float(exec_budget_s or 0)) if exec_budget_s else None
    exec_truncated = False
    # Position in `actions` from which nothing ran, so the scorer can tell an
    # item whose probe was PLANNED BUT NEVER REACHED (loop hit max_steps, the
    # wall-clock budget, or a `conclude`) apart from a static item that planned
    # no interaction at all. Both look like "zero steps" from the trajectory, but
    # only the first is an absence of evidence. `len(actions)` = the loop ran to
    # completion and nothing was stranded.
    _unreached_from = len(actions)
    # DEFECT 46 bookkeeping: the target of the click run in progress, and how many
    # clicks into it we are. Tracked across iterations because "the third click on
    # this canvas" is a property of the sequence, not of any one action.
    _canvas_run_target: str | None = None
    _canvas_run_n = 0
    for pos, action in enumerate(actions):
        if idx >= max_steps:
            # DEFECT 99: a step-budget stop is a truncation and must say so. Only
            # the wall-clock branch below used to set the flag, so a plan that ran
            # 40 of 72 actions reported `exec_truncated: False` — the trial looked
            # complete in the verdict, in the metrics and in the stdout, and the
            # twelve directional probes `_ensure_directional_steps` had appended
            # past the budget went missing with no signal at all (eval25_o5
            # `zxtyfcx`: 72-action plan, 40 executed, 11 items stranded, and the
            # two items that did resolve produced reward=1.0000).
            exec_truncated = True
            _unreached_from = pos
            break
        if _deadline is not None and time.monotonic() > _deadline:
            # Out of wall clock. Stop cleanly rather than letting the remaining
            # steps each burn their own caps: the evidence already captured is
            # kept, and the unrun items degrade to indeterminate (they have no
            # before/after pair), which by contract never gates.
            exec_truncated = True
            _unreached_from = pos
            break
        verb = action.get("action")
        # DEFECT 46: advance (or reset) the canvas click run. A `wait` between
        # clicks does NOT break the run — the planner writes `click, wait 200,
        # click, wait 200, click` for exactly the vertex sequence this fixes — but
        # any other verb might have changed the drawing mode (picking the ERASE
        # tool between two clicks), so the count restarts and the next click keeps
        # the centre it was aimed at.
        if verb == "click":
            _tgt = str(action.get("target") or "")
            if _tgt and _tgt == _canvas_run_target:
                _canvas_run_n += 1
            else:
                _canvas_run_target, _canvas_run_n = (_tgt or None), 0
        elif verb != "wait":
            _canvas_run_target, _canvas_run_n = None, 0
        rec: dict[str, Any] = {"step_no": idx, "action": verb,
                               "target": action.get("target", ""),
                               "note": action.get("note", ""),
                               "result": None, "error": None, "screenshot": None,
                               "dom_snapshot": "", "ax_diff": "", "console": []}
        if verb == "conclude":
            steps.append(rec)
            # `conclude` ends the run, so everything after it is stranded. The
            # planner no longer back-fills directional probes as `conclude`, but
            # a model-authored one mid-plan still truncates here.
            _unreached_from = pos + 1
            break
        if action.get("for_item") is not None:
            rec["for_item"] = action.get("for_item")
        if verb == "goto":
            # DEFECT 97. Addressed navigation, not guessed. Handled here rather
            # than in `browser_actions.ACTIONS` because the same-origin decision
            # needs `entry_url`, which the pure action executor does not have --
            # and an unguarded goto is the one verb that can silently move every
            # later probe onto someone else's page.
            #
            # Never a capture point (see `_NON_PROBE_VERBS`): a before/after pair
            # around a navigation measures "the page changed", which is true of
            # every navigation and evidence for no checklist item. The frame is
            # still taken and `prev_ax` re-baselined, so the NEXT step's `ax_diff`
            # describes what that step did to the new page rather than inheriting
            # the whole-document delta of arriving there.
            _raw_goto = str(action.get("url") or "")
            dest = resolve_same_origin(entry_url, _raw_goto)
            if dest is not None and _correct_ancestor_goto(entry_url, dest):
                # DEFECT 126: the resolved destination is a directory listing ABOVE
                # the artifact -- overwhelmingly the planner's reflexive first step
                # `goto "/"`. On a subpath-mounted artifact (entry
                # "/Reall3dViewer/dist/index.html") "/" is the static server's ROOT,
                # which serves a directory listing, not the app; following it
                # silently moves every later probe onto that listing and the whole
                # trial gates to 0.0 as "artifact does not render". The artifact
                # entry is known exactly, so CORRECT the aim to it. A foreign origin
                # already resolved to None above and is refused below; a sibling file
                # / sub-page is not an ancestor directory and navigates unchanged.
                rec["note"] = ((rec.get("note") or "")
                               + f" [goto {_raw_goto[:120]!r} pointed above the "
                                 "artifact; corrected to entry_url]").strip()
                dest = entry_url
            if dest is None:
                rec["error"] = ("goto refused (not same-origin as the artifact): "
                                + _raw_goto[:200])
            else:
                rec["target"] = dest
                navigate(page, dest)
                settle(page)
                rec["result"] = "ok"
            obs = observe(page)
            fname = _persist(obs.get("screenshot"), frames_dir,
                             f"{idx + 1:03d}_goto.png")
            if fname:
                frames.append(fname)
                rec["screenshot"] = fname
            rec["dom_snapshot"] = obs.get("dom_snapshot", "")
            rec["ax_diff"] = axtree.diff(prev_ax, obs.get("ax") or [])
            prev_ax = obs.get("ax") or prev_ax
            steps.append(rec)
            idx += 1
            continue
        _cap = action.get("capture") == "before_after"
        # How long to wait between the action and the AFTER frame. The plan's own
        # trailing `wait` is the generator TELLING US how long this effect needs:
        # "decreasing the rate to 0 empties the sky" is followed by wait ms=3000
        # because the emptying takes seconds. That wait currently runs after the
        # frame is already taken, so the probe photographs the un-settled state and
        # the judge — correctly, given that evidence — reports the effect did not
        # happen. `Dfxc2vB` was gated to 0.000 on exactly this: measured directly,
        # its meteor count falls 160 -> 0, but over ~12s, and the AFTER frame was
        # snapped 900ms in. Take the longer of the default dwell and what the plan
        # asked for, capped so one greedy plan cannot stall a worker.
        _dwell = PROBE_DWELL_MS
        if _cap:
            _next = actions[pos + 1] if pos + 1 < len(actions) else None
            if isinstance(_next, dict) and _next.get("action") == "wait":
                _ms = _next.get("ms")
                if isinstance(_ms, (int, float)):
                    _dwell = max(_dwell, min(int(_ms), PROBE_DWELL_MAX_MS))
        if _cap:
            settle(page)
            _pre = observe(page)
            _pre_name = _persist(_pre.get("screenshot"), frames_dir,
                                 f"{idx + 1:03d}_{_slug(rec.get('for_item', 'na'))}_before.png")
            if _pre_name:
                frames.append(_pre_name)
                rec["pre_frame"] = _pre_name
            # A directional item asserts what THIS action changed, so its diff
            # must bracket the action alone — re-baseline on the freshly settled
            # pre-state rather than on whatever the previous step left behind.
            prev_ax = _pre.get("ax") or prev_ax
        cap_ms = max(0, int(step_timeout_s)) * 1000
        if verb in ("drag", "scroll", "mouse_move"):
            # Resolve target-only coordinate actions before dispatch; see
            # _resolve_coord_action for why the raw action would KeyError. It
            # gets THIS page's budget, not a constant: its internal
            # scroll-into-view settles on consecutive stable FRAMES, so on a
            # 1.67fps page a fixed 1500ms cannot see two of them and the drag is
            # aimed at a stale below-the-fold box (v0.11 defect 32).
            action = _resolve_coord_action(page, action, action_timeout_ms)
            # `rec` copied the note before this ran, so a refusal recorded by the
            # press-point hit-test has to be carried across by hand.
            if action.get("note") and action["note"] != rec.get("note"):
                rec["note"] = action["note"]
        if verb == "wait":
            raw = action.get("ms")
            ms = cap_ms if not isinstance(raw, (int, float)) else min(int(raw), cap_ms)
            action = {**action, "ms": ms}
        elif verb in _SELECTOR_VERBS:
            # Two guards against selector actions dead-waiting on heavy pages:
            #   (1) skip the action entirely when the selector matches nothing — a
            #       planner-guessed selector that never resolves would else burn
            #       the full auto-wait on every miss;
            #   (2) cap the auto-wait at ACTION_TIMEOUT_MS (not step_timeout_s) so
            #       an element that is present-but-never-actionable (e.g. covered
            #       by a full-screen WebGL canvas) fails in ~2.5s, not 15–30s.
            # Before either, translate selector dialects Playwright rejects
            # outright (jQuery `:contains()`, accessibility-tree `button 'X'`) —
            # otherwise the existence probe swallows the SyntaxError, returns True,
            # and the action fails hard on a selector that was never parseable.
            target = normalize_selector(action.get("target") or "")
            if target != (action.get("target") or ""):
                action = {**action, "target": target}
                rec["target"] = target
                rec["note"] = (rec.get("note") or "") + " [normalized selector]"
            # DEFECT 44, click/hover/type half. `_query_measurable` pins a comma
            # union to its intended branch, but only the coordinate verbs go
            # through it — `_action_click` passes the raw string to `page.click`,
            # which resolves the union in DOCUMENT ORDER and clicks whatever the
            # trailing net matched first. Same defect, different verb: `36LuAgk`
            # clicked "PARAMS P" for a RESET probe. Pin it here so every verb
            # aims at the branch the planner meant.
            if target:
                pinned = _pin_union(page, target)
                if pinned != target:
                    target = pinned
                    action = {**action, "target": pinned}
                    rec["target"] = pinned
                    rec["note"] = ((rec.get("note") or "")
                                   + " [pinned union to matching branch]")
                # DEFECT 56: the matches NEST, so document order hands back the
                # container that merely contains the target. Runs AFTER the union
                # pin (a comma list must be one branch before it can be wrapped in
                # `:has()`) and BEFORE the tab narrowing, so the already-selected
                # tab test is applied to the innermost candidates.
                nested = _pin_nested(page, target)
                if nested != target:
                    target = nested
                    action = {**action, "target": nested}
                    rec["target"] = nested
                    rec["note"] = ((rec.get("note") or "")
                                   + " [pinned to innermost match]")
                # DEFECT 74: the innermost match OF THE PLANNER'S TAG is still a
                # container when the real control is a different tag. Runs AFTER
                # `_pin_nested` (it accepts and re-pins that rewrite) and BEFORE
                # the tab/prose narrowing, so those see a selector that already
                # aims at a control rather than at a 209-descendant wrapper.
                tagfree = _pin_tagfree(page, target)
                if tagfree != target:
                    target = tagfree
                    action = {**action, "target": tagfree}
                    rec["target"] = tagfree
                    rec["note"] = ((rec.get("note") or "")
                                   + " [re-pinned tag-free into control]")
                # DEFECT 50: still ambiguous after pinning, and the first match
                # is an already-selected tab whose click is a no-op. Runs AFTER
                # the union pin so a union that resolved to one branch is left
                # alone, and only for the verbs that act on the element itself.
                if verb in ("click", "hover"):
                    narrowed = _pin_ambiguous(page, target)
                    if narrowed != target:
                        target = narrowed
                        action = {**action, "target": narrowed}
                        rec["target"] = narrowed
                        rec["note"] = ((rec.get("note") or "")
                                       + " [skipped already-selected tab]")
                    # DEFECT 68: still ambiguous, and the first match is inert
                    # PROSE naming the control ("click Launch Assistant, then
                    # Start tour") while the real control matches later. Runs
                    # LAST of the four pins: the ones above narrow by structure
                    # and can remove the offending prose on their own, and this
                    # one is an `nth=` index, which must be computed against the
                    # final match set or it points at the wrong element.
                    pinned_prose = _pin_prose(page, target)
                    if pinned_prose != target:
                        target = pinned_prose
                        action = {**action, "target": pinned_prose}
                        rec["target"] = pinned_prose
                        rec["note"] = ((rec.get("note") or "")
                                       + " [pinned past non-interactive prose]")
            if target and not _selector_present(page, target):
                # DEFECT 118: FIRST, and for EVERY selector verb, try to salvage a
                # planner label that was mangled from the accessible name it read
                # (space<->underscore, a runtime counter suffix, a composed name).
                # This returns a concrete selector for a REAL live control whose
                # name matches the label under normalisation, so re-pointing
                # `target` at it lets the ordinary action (type/select/click) run
                # against a genuine element — no manufactured evidence, and it is
                # the only salvage that fires for `type`/`select`. Runs before the
                # click-only tiers so a normalisable miss never degrades to a
                # native dispatch or a note-guess.
                lnsel = _label_normalized_selector(page, target)
                if lnsel and _selector_present(page, lnsel):
                    target = lnsel
                    action = {**action, "target": lnsel}
                    rec["target"] = lnsel
                    rec["note"] = ((rec.get("note") or "")
                                   + " [label-normalized selector]")
            if target and not _selector_present(page, target):
                # Before giving up on a missing selector, try to salvage the
                # planner's INTENT for a click: if the selector carried a
                # :has-text("X") predicate, the label the planner saw is real
                # even when its guessed tag/class is wrong (div-row game menus:
                # `div.menu-item:has-text("Single Heat")` misses because the live
                # element is `div.mrow`). Relax to the text alone and native-
                # dispatch a click on it — this is how a div-menu game actually
                # advances past its title screen. Only for `click` (hover/type on
                # a wrong element are not worth guessing).
                if verb == "click" and _try_relaxed_text_click(page, target):
                    rec["result"] = "ok"
                    rec["note"] = (rec.get("note") or "") + " [relaxed :has-text click]"
                else:
                    # DEFECT 60: last resort, and only for a click. The tiers
                    # above need a `:has-text` predicate; 12% of missing
                    # selectors have none, so nothing fires and the item loses
                    # its evidence. The planner's `note` describes the intent in
                    # words ("Open the side menu") -- match it against visible
                    # accessible names, but only when the answer is UNIQUE and
                    # shares a real noun. `_note_match` returns None on a tie by
                    # design: clicking the opposite control turns a recoverable
                    # indeterminate into a confident wrong verdict.
                    nsel = (_note_match_relaxed(page, str(rec.get("note") or ""))
                            if verb == "click" else None)
                    src = "note"
                    if not nsel and verb == "click" and not _CSS_ISH.search(target):
                        # DEFECT 60b: when the note is not prose, the TARGET
                        # often is. The planner writes `angle slider`, `START
                        # RUN`, `Serve` -- a label, not a selector -- and the
                        # census found this is 29% of the targets no tier can
                        # salvage. It is strictly better evidence than the note:
                        # the note describes intent, the target names the
                        # control. Gated on the target not looking like CSS, so
                        # a genuine-but-stale `#foo .bar` is never tokenized
                        # into a guess, and it runs through the same five rules.
                        nsel = _note_match(page, target)
                        src = "target"
                    if nsel:
                        try:
                            page.dispatch_event(nsel, "click",
                                                timeout=ACTION_TIMEOUT_MS)
                            rec["result"] = "ok"
                            rec["note"] = ((rec.get("note") or "")
                                           + f" [{src}-matched click]")
                        except Exception:  # noqa: BLE001
                            rec["error"] = f"selector not found: {target}"
                    else:
                        rec["error"] = f"selector not found: {target}"
                obs = observe(page)
                fname = _persist(obs.get("screenshot"), frames_dir,
                                 f"{idx + 1:03d}_{verb}.png")
                if fname:
                    frames.append(fname)
                    rec["screenshot"] = fname
                rec["dom_snapshot"] = obs.get("dom_snapshot", "")
                rec["ax_diff"] = axtree.diff(prev_ax, obs.get("ax") or [])
                prev_ax = obs.get("ax") or prev_ax
                steps.append(rec)
                idx += 1
                continue
            action = {**action, "timeout": min(action_timeout_ms, cap_ms)}
            # DEFECT 46: give each repeated click on one canvas its own pixel.
            # `_canvas_run_n` was advanced at the top of the loop; 0 means this is
            # the first click of a run and keeps Playwright's centre-aiming.
            if verb == "click" and target and not action.get("position"):
                pos = _canvas_repeat_position(page, target, _canvas_run_n)
                if pos is not None:
                    action = {**action, "position": pos}
                    rec["position"] = pos
                    rec["note"] = ((rec.get("note") or "")
                                   + f" [auto: canvas click #{_canvas_run_n + 1}"
                                     " offset from centre]").strip()
        if verb == "scroll":
            # Not a plain dispatch: verify the page actually moved and fall back
            # to a scripted scroll when the wheel is swallowed. See execute_scroll.
            res = execute_scroll(page, action)
            if res.get("scroll"):
                rec["scroll"] = res["scroll"]
        else:
            res = browser_actions.execute(page, action)
        # Force-retry for selector verbs that time out on actionability. Game
        # menus animate continuously (looping background, pulsing CTA), so the
        # element's box never stops moving and Playwright's *stability* gate
        # never settles — every click dead-waits the cap and fails, the game
        # never launches, and it scores a blank menu regardless of quality. A
        # real user clicks an animating button fine; retry once with force=True
        # to bypass the visible/stable/enabled/hit-test gates. Only on a genuine
        # actionability timeout (element resolved but never became clickable),
        # never on a "selector not found" — a missing selector stays missing.
        #
        # But force ALSO bypasses the hit-test gate, and that is not a bypass we
        # can afford when the element is OCCLUDED. Forcing dispatches at the
        # element's coordinates regardless of what is painted there, so the
        # event lands on the overlay on top and Playwright returns no error: the
        # step records `ok` having done nothing. That is the worst outcome in
        # the system — a failed step degrades its item to indeterminate, but a
        # FAKE SUCCESS lets the judge gate a working artifact at 0.0 on evidence
        # of an interaction that never happened. `dLmjAh5`: the click on the
        # panel's `#bDemo` was force-retried while a fixed full-viewport `#gate`
        # covered it, reported ok, and the item "DEMO source button activates
        # signal" was judged False. Clicking the real gate button instead drives
        # the analyser to SUB 79 / BASS 76 / MID 80, RMS -12 db — the artifact
        # was correct throughout.
        #
        # Playwright names the two cases apart in its call log ("intercepts
        # pointer events" vs. a stability timeout), so only force the animating
        # one and let occlusion stand as an honest error.
        if (verb in _SELECTOR_VERBS and "error" in res
                and "Timeout" in str(res.get("error", ""))):
            _err = str(res.get("error", ""))
            if (_NOT_ENABLED_RE.search(_err) or _never_resolved(_err)
                    or _resolved_but_unattempted(_err)):
                # Still booting, not broken — the element exists but is disabled,
                # or it is not in the DOM yet, or it resolved and then no
                # actionability verdict ever came back. All three are states the
                # page can leave on its own. Give it a longer budget before giving
                # up; see ACTION_RETRY_TIMEOUT_MS, _never_resolved and
                # _resolved_but_unattempted.
                if _NOT_ENABLED_RE.search(_err):
                    _why = "element was still disabled"
                elif _never_resolved(_err):
                    _why = "selector had not appeared yet"
                else:
                    _why = "no actionability verdict came back in time"
                # The retry must actually be longer than the try. On a 2 fps page
                # the scaled budget is already 10s, and a "retry" at the fixed
                # 8000ms would be SHORTER than the attempt that just failed —
                # the same inversion that made the disclosure rescue useless
                # under load (see DISCLOSURE_CLICK_TIMEOUT_MS). Scale the retry
                # from whichever is larger.
                _retry_ms = max(ACTION_RETRY_TIMEOUT_MS,
                                int(action_timeout_ms * ACTION_RETRY_TIMEOUT_MS
                                    / max(1, ACTION_TIMEOUT_MS)))
                waited = browser_actions.execute(
                    page, {**action, "timeout": _retry_ms})
                if "error" not in waited:
                    res = waited
                    rec["note"] = ((rec.get("note") or "")
                                   + f" [re-waited: {_why}]")
                else:
                    res = waited
            elif _INVISIBLE_RE.search(_err) and (
                    _lbl := _visible_label_for(page, target)):
                # The styled-checkbox idiom: the real <input> is CSS-hidden and
                # the visible control is its <label>. Force cannot click a box
                # that does not exist, so click what a human clicks and let the
                # browser forward it. See _INVISIBLE_RE and _visible_label_for —
                # both of which decline unless this is exactly that shape.
                relabelled = browser_actions.execute(
                    page, {**action, "target": _lbl,
                           "timeout": ACTION_RETRY_TIMEOUT_MS})
                if "error" not in relabelled:
                    res = relabelled
                    rec["note"] = ((rec.get("note") or "")
                                   + " [label-clicked: control is CSS-hidden]")
                # On failure `res` is left alone: the original "element is not
                # visible" names the real problem, whereas the label's error
                # would report a selector the plan never mentioned.
            elif _OCCLUDED_RE.search(_err):
                # DEFECT 64: before declining, check whether the occluder is one
                # of the elements THIS selector already matched. If it is, the
                # selector was ambiguous and Playwright's first-in-document pick
                # was simply the wrong one of the N — so click the one actually
                # on top. See _occluding_sibling_match, which returns None for
                # every other shape of occlusion.
                _top = _occluding_sibling_match(page, target)
                _restacked = (browser_actions.execute(
                    page, {**action, "target": _top,
                           "timeout": ACTION_RETRY_TIMEOUT_MS})
                    if _top else {"error": "no matched occluder"})
                if "error" not in _restacked:
                    res = _restacked
                    rec["note"] = ((rec.get("note") or "")
                                   + " [clicked occluding match: selector was"
                                     " ambiguous]")
                else:
                    # DEFECT 70: the occluder is not one of our matches, so
                    # something really is painted over the target — but that
                    # something is usually a transient overlay an EARLIER probe
                    # opened (a tour dialog, a modal scrim, an open side nav),
                    # and it is dismissible. Try Escape, its own close control,
                    # then its scrim; if any works, retry the real action on the
                    # now-unobstructed page. See `_dismiss_blocking_overlay`,
                    # which only ever clicks inside the overlay's own subtree.
                    _how = _dismiss_blocking_overlay(page)
                    _after = (browser_actions.execute(
                        page, {**action, "timeout": ACTION_RETRY_TIMEOUT_MS})
                        if _how else {"error": "no dismissible overlay"})
                    if "error" not in _after:
                        res = _after
                        rec["note"] = ((rec.get("note") or "")
                                       + f" [dismissed blocking overlay via"
                                         f" {_how}]")
                    else:
                        # Unchanged behaviour: an overlay that survives Escape,
                        # its own close button AND its scrim is a real finding
                        # about the artifact, and forcing would report a click
                        # that never reached anything.
                        rec["note"] = ((rec.get("note") or "")
                                       + " [not force-clicked: target is"
                                         " occluded]"
                                       + ("" if _how else
                                          " [no dismissible overlay found]"))
            else:
                forced = browser_actions.execute(page, {**action, "force": True})
                if "error" not in forced:
                    res = forced
                    rec["note"] = (rec.get("note") or "") + " [force-clicked: animating menu]"
        if "error" in res:
            rec["error"] = res["error"]
        else:
            rec["result"] = res.get("result", "ok")
            # For `advance`, record which control it clicked so a trajectory is
            # debuggable after the fact (otherwise you can't tell whether advance
            # hit the launch button, a wrong control, or no-op'd).
            if "advanced_to" in res:
                rec["advanced_to"] = res["advanced_to"]
        # A before_after probe is judged on the CHANGE its action produced, so
        # let that change actually land before observing. Without this the AFTER
        # frame is taken in the same tick as the action and a slider that
        # repopulates a particle field over ~1s photographs as "nothing moved"
        # (b0811, user remark: "extending the waiting period to let the changes
        # more obvious"). Guarded: a stub page has no wait_for_timeout.
        if _cap and not rec.get("error") and _dwell > 0:
            # DEFECT 67: sample the dwell instead of sleeping through it. Same
            # window, same end instant — the AFTER frame below still lands where
            # it did before — but the intermediate frames turn "did the endpoints
            # differ" into "how did it evolve", which is the question a `flowing`,
            # an `orbits`, or a `Pause stops it` check is actually asking. On
            # failure the burst returns [] and the pre/post pair is unaffected.
            _burst = _capture_burst(
                page, frames_dir, f"{idx + 1:03d}_{_slug(rec.get('for_item', 'na'))}",
                _dwell)
            if _burst:
                # NOT appended to `frames`: that list feeds the shared 12-frame
                # sample every non-directional item is judged on, and a burst is
                # by construction a run of near-identical frames from one moment
                # of one step. Uniform-sampling 12 out of a list those dominate
                # would spend most of the shared evidence on one probe's dwell.
                # The burst is per-item evidence and reaches the judge through
                # `burst_frames` on this step.
                rec["burst_frames"] = _burst
            else:
                try:
                    page.wait_for_timeout(_dwell)
                except Exception:  # noqa: BLE001
                    pass
            rec["probe_dwell_ms"] = _dwell
        obs = observe(page)
        fname = _persist(obs.get("screenshot"), frames_dir,
                         f"{idx + 1:03d}_{verb}.png")
        if fname:
            frames.append(fname)
            rec["screenshot"] = fname
        rec["dom_snapshot"] = obs.get("dom_snapshot", "")
        # What this action changed, as +/- AX lines. This is the judge's primary
        # state evidence: innerText cannot show a modal opening, an aria-expanded
        # flip, or a slider's valuenow moving, and the judge kept reading that
        # absence as "the DOM remained unchanged" for interactions that worked.
        rec["ax_diff"] = axtree.diff(prev_ax, obs.get("ax") or [])
        prev_ax = obs.get("ax") or prev_ax
        # Post-capture ONLY when the action actually ran. An errored action is a
        # no-op, so a pre/post pair bracketing it is a pair of IDENTICAL frames —
        # the judge reads "no change" and returns a DETERMINATE `satisfied: False`,
        # which can force a critical gate (score 0.0) on evidence that never
        # existed. With no post_frame the scorer has no usable pair and the item
        # degrades to indeterminate, which never gates (spec: DEGRADE-NEVER-GATE).
        if _cap and not rec.get("error"):
            _post_name = _persist(obs.get("screenshot"), frames_dir,
                                  f"{idx + 1:03d}_{_slug(rec.get('for_item', 'na'))}_after.png")
            if _post_name:
                frames.append(_post_name)
                rec["post_frame"] = _post_name
        steps.append(rec)
        idx += 1
        if res.get("result") == "conclude":
            break

    # aesthetics evidence: desktop always; mobile only if the task needs it.
    desktop = capture_viewport_shot(page, frames_dir, "desktop.png",
                                    width=DESKTOP_VP[0], height=DESKTOP_VP[1])
    mobile = None
    if needs_mobile:
        mobile = capture_viewport_shot(page, frames_dir, "mobile.png",
                                       width=MOBILE_VP[0], height=MOBILE_VP[1])
        # restore desktop viewport so nothing downstream sees a mobile page.
        # Bounded — same DEFECT-120 hazard as `capture_viewport_shot`'s own
        # `set_viewport_size` above (see there). On timeout the viewport stays
        # at MOBILE; the trajectory is already ending so nothing downstream
        # reads the page again.
        def _restore_viewport() -> None:
            try:
                page.set_viewport_size({"width": DESKTOP_VP[0], "height": DESKTOP_VP[1]})
            except Exception:  # noqa: BLE001 - stub page or wedge, both survivable
                pass
        _restore = threading.Thread(target=_restore_viewport,
                                    name="viewport-restore", daemon=True)
        _restore.start()
        _restore.join(3.0)
    interaction = [f for f in frames
                   if f not in ("000_initial.png", "desktop.png", "mobile.png")]
    # NOTE: desktop/mobile shots live in `evidence` ONLY. They are deliberately
    # NOT appended to `frames` — the functional scorer consumes `frames` and
    # `select_frames` always keeps the last one, so appending a 390x844 mobile
    # render would perturb the functional judge (spec: `frames` stays unchanged
    # for back-compat; aesthetics reads its shots from `evidence`).
    evidence = {"desktop": desktop, "mobile": mobile, "interaction": interaction}

    # Late-paint rescue for `renderable` (see the note where it is first set).
    # The gate is one-directional on purpose: a later painted frame can only
    # promote False -> True, never demote. A blank frame mid-run is normal (a
    # fade, a scene transition, a modal), so treating blankness as a demotion
    # would invent a non-renderability that the load-time check did not find.
    # We look at the LAST few frames rather than any frame: an artifact that
    # painted once and then went blank is not what we are rescuing, and `desktop`
    # is the most authoritative — it is a full-viewport shot taken after the
    # whole run, on a page that has had every chance to boot.
    if not renderable:
        for _cand in ([desktop] if desktop else []) + list(reversed(interaction))[:4]:
            _blank = _frame_is_blank(str(frames_dir / _cand))
            if _blank is False:
                renderable = True
                build_defects.append({
                    "kind": "late_paint",
                    "detail": (f"blank at load, painted by {_cand}: the artifact "
                               "boots slower than the load-time settle"),
                })
                break

    # Re-sweep for unreachable dependencies now that the run is over. The
    # load-time pass above only sees requests that failed before `settle`, but a
    # module graph loaded via dynamic `import()` fails LATER — b0811 `aAXf3Hj`,
    # `EJtifV4` and `h8pdb6B` all died on `ERR_CONNECTION_RESET` fetching
    # three.js and were recorded with `build_defects: []`, so 12% of the pilot's
    # scored artifacts took a 0.0 that reads as bad authoring when it is really
    # a sandbox with no route to the CDN. Selection must be able to tell those
    # apart, so the sweep runs again against every request that failed all run.
    for _d in detect_cdn_defects(failed_requests, entry_url):
        if _d.get("detail") not in _cdn_hosts_at_load:
            build_defects.append(_d)
    for _d in detect_missing_asset_defects(bad_responses, entry_url):
        if _d.get("detail") not in _missing_at_load:
            build_defects.append(_d)

    return {
        "entry_url": entry_url,
        "renderable": renderable,
        "steps": steps,
        "frames": frames,
        # The page's accessibility tree at load and after the last action, as
        # text. This is what the judge reads INSTEAD of the source: it is the
        # structure and state a human sees (headings, controls, labels, checked/
        # expanded/valuenow), it is complete where a code slice was truncated,
        # and it is ~4x smaller than the code block it replaces.
        "ax_initial": axtree.to_text(init.get("ax") or []),
        "ax_final": axtree.to_text(prev_ax),
        # True when the wall-clock exec budget stopped the loop early. Surfaced
        # so a low score on a truncated trajectory is legible as partial coverage
        # rather than read as a bad artifact.
        "exec_truncated": exec_truncated,
        # The per-action click budget this artifact was actually given, in ms.
        # Recorded because it is derived from the page's own frame rate at load,
        # so it is the one number that explains a run's actionability timeouts
        # after the fact — 2500 means "this page paints fast enough for the
        # floor", 10000 means "this page paints at 2 fps and every click costs
        # accordingly". See _frame_scaled_timeout_ms.
        "action_timeout_ms": action_timeout_ms,
        "steps_planned": len(actions),
        # Items whose steps were planned but stranded in the tail the loop never
        # reached. The scorer cannot derive this: from the trajectory alone, an
        # item with zero steps looks identical whether its probe was skipped or
        # whether it is a static check that never planned an interaction. Only
        # the first is an absence of evidence, and conflating them either lets a
        # never-attempted check gate the artifact or makes every static check
        # unjudgeable.
        "unreached_items": sorted({str(a.get("for_item"))
                                   for a in actions[_unreached_from:]
                                   if a.get("for_item") is not None}),
        "video": None,  # set by the run layer when a context video exists
        "console_errors": console_errors[:20],
        "page_errors": page_errors[:20],
        "evidence": evidence,
        "build_defects": build_defects,  # forced-visible overlay / CDN-unreachable
    }
