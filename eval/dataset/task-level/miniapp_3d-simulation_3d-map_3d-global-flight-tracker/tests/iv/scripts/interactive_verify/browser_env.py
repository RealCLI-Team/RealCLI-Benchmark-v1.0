#!/usr/bin/env python3
"""Chromium launch arguments for deterministic headless WebGL.

Racing/3D games in the b0811 testbed build their scene with a three.js
``WebGLRenderer`` and hard-fail to a "NO WEBGL" fallback sheet the instant the
GL context can't be created. Plain ``chromium.launch(headless=True)`` lets
Chromium auto-pick a GL backend; in a GPU-less container that auto-pick is
non-deterministic — the same artifact renders fine in an ad-hoc load yet lands
in the NO-WEBGL fallback under the full verifier process, so an entire class of
3D games scores ~0 for a reason that has nothing to do with build quality. That
is the "games verify unreliably" symptom the testbed exists to fix.

The fix mirrors what htmleval / ArtifactsBench do for headless 3D: stop relying
on auto-detection and force the software rasterizer (SwiftShader via ANGLE)
explicitly, so every worker gets the SAME working WebGL2 backend every run.
SwiftShader is CPU-only (slower than a real GPU) but present and deterministic,
which is exactly the tradeoff a verifier wants.

``chromium_launch_args()`` is the single source of truth for both browser
launch sites (``run.py`` executor factory and ``harbor_loader.py`` render/AX
probe). Override via ``IV_CHROMIUM_ARGS`` (space-separated) to experiment
without code changes, or ``IV_CHROMIUM_ARGS=""`` to force bare auto-detection.

``launch_guarded()`` lives here for the same reason: pairing the args with the
watchdog is the other thing every launch site must do, and a single helper that
does both is the only version of that pairing a third launch site cannot forget.
"""
from __future__ import annotations

import itertools
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

# Software-GL backend flags. This set is copied from the repo's own proven
# headless-WebGL config — the vendored htmleval BrowserPool (see
# service/verify/vendor/htmleval/concurrency/browser_pool.py:30), which renders
# the `three_3d` benchmark category reliably in this same GPU-less container.
#
# The subtle, load-bearing flag is `--disable-gpu`: without it Chromium still
# probes for a hardware GL path first and only falls back to software on
# failure. In a GPU-less container that probe fails *non-deterministically*, and
# when its async fallback loses the race against the artifact's three.js
# `WebGLRenderer` construction the game drops into its "NO WEBGL" sheet — the
# exact run-to-run flakiness we saw (scores 0.136/0.163/0.0/0.093 on identical
# reruns). `--disable-gpu` skips the hardware probe entirely so every worker
# takes the same SwiftShader path every run.
#
# `--use-angle=swiftshader` selects ANGLE's SwiftShader (software) GL backend;
# `--enable-webgl` + `--ignore-gpu-blocklist` keep WebGL on for "unsupported"
# virtual GPUs; `--enable-unsafe-swiftshader` is required on recent Chromium to
# permit SwiftShader-backed WebGL at all (without it WebGL silently degrades
# under headless). We intentionally do NOT pass `--use-gl=angle`: htmleval omits
# it, and combined with `--disable-gpu` it was part of the flaky config above.
# `--no-sandbox` + `--disable-dev-shm-usage` match htmleval for container safety.
_DEFAULT_ARGS = (
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--ignore-gpu-blocklist",
    "--enable-webgl",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    # `--disable-gpu-sandbox` is what actually makes the four flags above WORK in
    # this container, and its absence is invisible until you read Chromium's own
    # GPU log. `--no-sandbox` disables the *renderer* sandbox only; the GPU
    # process keeps its own, and under that sandbox the dlopen of the bundled
    # SwiftShader GL library fails:
    #
    #   ERROR:ui/ozone/common/egl_util.cc:58] Failed to load GLES library:
    #     .../chrome-linux64/libGLESv2.so: cannot open shared object file:
    #     Permission denied
    #   VERBOSE1:gpu/ipc/service/gpu_init.cc:502] InitializeStaticGLBindingsOneOff failed
    #   ERROR:.../viz_main_impl.cc:190] Exiting GPU process due to errors during initialization
    #
    # The file is 0755 and dlopens fine from Python as the same uid, so this is
    # the sandbox policy, not the filesystem. The GPU process then dies and
    # Chromium serves every page with NO WebGL at all — `getContext('webgl2')`
    # returns null, so 3D artifacts render their "WEBGL UNAVAILABLE" fallback
    # sheet and the judge correctly-but-uselessly fails them. That is a VERIFIER
    # environment fault presenting as an artifact defect: the b0811 acceptance
    # gate re-scored two audited 3D tasks to 0.0 on `critical:WebGL2 context
    # initializes successfully` for artifacts a human could see rendering fine.
    #
    # Measured on this box (chromium 1228): without it `getParameter(RENDERER)`
    # is unreachable (no context); with it, 'WebKit WebGL' / 'WebKit' — the
    # SwiftShader path we intend. Safe here for the same reason `--no-sandbox`
    # is: the browser only ever loads local artifact files we extracted.
    "--disable-gpu-sandbox",
    # Hard ceiling on the renderer's JS heap. An artifact that allocates without
    # bound (b0810 `UUznbn6`, a 3D fluid sim, reached 10.3GB RSS and was still
    # climbing 38 minutes in) otherwise takes the whole box down: at 64 workers a
    # handful of these is an OOM, not a slow task. Chromium enforces this inside
    # V8, so the tab dies with an OOM the harness sees as a page error while the
    # sweep keeps going. 2048MB is far above what any legitimate artifact in this
    # corpus needs (normal renderers sit in the low hundreds of MB) and well
    # below the per-worker share of available RAM.
    "--js-flags=--max-old-space-size=2048",
)


def chromium_launch_args() -> list[str]:
    """Return the Chromium ``args`` list for a headless launch.

    Defaults to the SwiftShader software-GL flags above. If the env var
    ``IV_CHROMIUM_ARGS`` is set, its whitespace-split value replaces the default
    (empty string → no extra args, i.e. Chromium's own auto-detection)."""
    override = os.environ.get("IV_CHROMIUM_ARGS")
    if override is not None:
        return override.split()
    return list(_DEFAULT_ARGS)


# Wall-clock ceiling on a single browser process, and how much resident memory
# one artifact may hold. These are the LAST line of defence, below the executor's
# own exec_budget_s: that budget is only checked BETWEEN steps, so it cannot fire
# while a single Playwright call is blocked in a renderer that has stopped
# responding. On b0810, `fE7HFu6` sat 37 minutes inside one call having produced
# zero frames, and `UUznbn6` (a 3D fluid sim) climbed to 10.3GB RSS and was still
# growing — with 64 workers a handful of either is a stalled sweep or an OOM box.
#
# The deadline runs from LAUNCH, and the browser stays open across the whole
# task — plan, execute, score and aesthetics — not just execution. So it must
# clear the sum of those phases, not EXEC_BUDGET_S alone. At 1200s it did not:
# measured over post-restart tasks, browser lifetime ran p50=1222s / p90=1347s
# on b0811, so the watchdog was firing on about half of all HEALTHY tasks (22
# kills in one 30-minute window, every one on the time limit) and each kill
# threw away a complete task with "Target page, context or browser has been
# closed". 3600s sits ~2.7x above the observed p90 while still catching the
# 4165s wedges this exists for; a real wedge produces zero frames and is caught
# either way, just later.
BROWSER_HARD_LIMIT_S = float(os.environ.get("IV_BROWSER_HARD_LIMIT_S", "3600"))
BROWSER_RSS_LIMIT_MB = float(os.environ.get("IV_BROWSER_RSS_LIMIT_MB", "4096"))

# DEFECT 107: a runaway renderer starves the whole lane, and neither limit above
# sees it. Measured on the s00 tail shard, one artifact's renderer sat at 1090%
# CPU -- 11 of the 48-core quota on ONE task -- with modest RSS and well inside
# the 3600s deadline, while its three sibling slots crawled. This is the
# unbounded-`page.evaluate`-on-a-pegged-renderer shape: the artifact spins
# (rAF loop, `while(true)`, a physics step that never converges) and CDP replies
# never come, so our call has nothing to time out against.
#
# The threshold is per-BROWSER-TREE, in cores, and 4.0 is deliberately generous:
# a legitimate WebGL artifact uses the GPU process plus a renderer plus a
# compositor, and 2-3 cores of genuine parallel work is normal for the voxel/3D
# families. Sustained means SUSTAINED -- averaged across a window, not sampled --
# because a single spike during scene init is not a runaway.
BROWSER_CPU_LIMIT_CORES = float(os.environ.get("IV_BROWSER_CPU_LIMIT_CORES", "4.0"))
BROWSER_CPU_WINDOW_S = float(os.environ.get("IV_BROWSER_CPU_WINDOW_S", "180"))

# v0.70 — per-task 3D uplift. The two limits above are sized for a plain
# artifact, but a legitimate heavy-3D render (a WebGL fluid sim, a voxel world, a
# three.js scene) sustains 5-8 cores and can balloon past 4GB, so the same limbs
# that catch a `while(true)` runaway also guillotine a healthy 3D task. When the
# loader statically detects a 3D artifact (see `detect_heavy_3d`), the watchdog
# for THAT tree reads these higher ceilings instead — a STRICT RELAXATION: the
# kill decision uses `max(base, uplift)`, so a non-3D tree is unaffected to the
# bit and the worst case anywhere is a wrongly-HIGH ceiling for one task, never a
# wrongly-low kill. The 3600s time limb (BROWSER_HARD_LIMIT_S) is NOT uplifted, so
# a wedged 3D tree is still caught at the deadline. 16 cores = 1/6 of the 96-core
# box, above the 5-8-core heavy band; 8192MB = 2x base, below the ~10GB fluid-sim
# balloon the base RSS limit exists to catch (so a genuine leak is still caught,
# just later). Applies to BOTH entry points (qa + Harbor) — one shared watchdog.
BROWSER_CPU_LIMIT_3D_CORES = float(os.environ.get("IV_BROWSER_CPU_LIMIT_3D_CORES", "16"))
BROWSER_RSS_LIMIT_3D_MB = float(os.environ.get("IV_BROWSER_RSS_LIMIT_3D_MB", "8192"))

# Marker switch, unique per launch, used to FIND the browser process. See
# `launch_guarded`. Chromium ignores unrecognized `--flags`, and passes them
# down to its children, so the whole tree is findable by one grep of cmdlines.
_MARKER_PREFIX = "--iv-watchdog-id="
_marker_seq = itertools.count()


def _find_marked_pids(marker: str) -> list[int]:
    """PIDs of the browser process(es) launched with ``marker``, plus their children.

    Chromium puts the marker only on the BROWSER process command line — renderer,
    GPU and zygote children are spawned with `--type=...` switches and do NOT
    inherit it (verified against a real launch). So the marker locates the root
    and psutil walks down from there, which is what the RSS check needs: a
    leaking artifact's memory lives in the renderer child, not the root.

    The match must be on the WHOLE argv entry, never a substring. Markers are
    `--iv-watchdog-id=<pid>-<seq>`, so a substring test makes `…-1` match `…-10`,
    `…-11`, `…-19` as well: with 16 workers the watchdog armed for one browser
    saw a dozen unrelated ones as its own "tree", summed their RSS, sailed past
    the limit and killed them all. Observed live — kill lines listing 44 and 61
    processes (a real tree is ~6), and every task after the first kill failing
    with "Target page, context or browser has been closed".

    Self and our own ancestors are excluded. A process whose command line merely
    contains the marker string would otherwise match — including the shell or
    driver that spawned us — and killing the sweep driver would be far worse
    than the stall the watchdog exists to prevent."""
    import psutil
    try:
        me = psutil.Process()
        forbidden = {me.pid} | {a.pid for a in me.parents()}
    except Exception:
        forbidden = set()
    roots = []
    for pr in psutil.process_iter(["pid", "cmdline"]):
        try:
            if pr.info["pid"] in forbidden:
                continue
            if any(a == marker for a in (pr.info.get("cmdline") or [])):
                roots.append(pr)
        except Exception:  # process died mid-iteration / access denied
            continue
    out = []
    for r in roots:
        out.append(r.pid)
        try:
            out.extend(c.pid for c in r.children(recursive=True))
        except Exception:
            continue
    return sorted(set(out))


def _driver_pids(browser_pids: list[int]) -> list[int]:
    """PIDs of the Playwright node driver(s) owning ``browser_pids``.

    Killing Chromium alone is NOT enough, and the gap cost this sweep two of six
    lanes. Ordinary page calls do notice their browser dying — the driver rejects
    them with `TargetClosedError` — but `CDPSession.detach()` gets no such
    rejection: it waits for a reply from a target that no longer exists, and the
    sync API accepts no timeout, so it never returns AND never raises. The
    `except Exception: pass` wrapped around it (axtree.py, executor.py) therefore
    never runs. The worker thread is lost for the life of the process, holding its
    `pool.map` slot open forever.

    Measured live on the 44k Q&A sweep: 31 watchdog kills produced 28 permanently
    wedged worker slots (per lane, kills 8/5/4/3/8/3 against wedged 7/4/4/2/8/3),
    two lanes at exactly zero throughput, and effective concurrency of 20-24 of a
    nominal 48 — visible ONLY as a slow decline in tasks/h, with no traceback, no
    `exited rc=`, and every lane process still alive. Reproduced in isolation and
    confirmed both ways: `detach()` after a chrome-only kill is still blocked at
    100s, and killing the driver frees it immediately (it then raises, which the
    existing handler absorbs).

    The driver is the PARENT of the marked browser root, and one driver serves
    exactly one worker slot (each thread starts its own `sync_playwright()`), so
    killing it can never disturb a sibling worker. Our own process and ancestors
    are excluded — the driver is a child of ours, never an ancestor."""
    import psutil
    try:
        me = psutil.Process()
        forbidden = {me.pid} | {a.pid for a in me.parents()}
    except Exception:
        forbidden = set()
    out = set()
    for pid in browser_pids:
        try:
            par = psutil.Process(pid).parent()
        except Exception:
            continue
        if par is None or par.pid in forbidden:
            continue
        try:
            # Only ever the Playwright node driver, never some other ancestor:
            # match on the driver path Playwright itself execs.
            if any("playwright" in a and "driver" in a
                   for a in (par.cmdline() or [])):
                out.add(par.pid)
        except Exception:
            continue
    return sorted(out)


# How many worker slots may be permanently wedged before the process gives up
# and lets its supervisor restart it, and how long a thread must sit in the same
# unbounded call before it counts as wedged rather than merely slow.
#
# DEFECT 106: an ABSOLUTE count is unreachable on a small pool, and defect 105's
# fix made small pools the common case. Sizing the pool to the work
# (`min(workers, len(ids))`) means a tail shard runs 4 slots, so a 3-slot
# threshold cannot fire until 75% of the pool is dead -- and 2 of 4 wedged, which
# is exactly what was measured on the s00 tail shard, is invisible forever. The
# fraction is what we actually care about: a lane that has lost half its slots is
# doing half the work while every external signal reports health. The absolute
# floor stays because 1 wedged slot in a 2-slot pool is a coin flip, not a
# pattern, and restarting on it risks a loop.
REAPER_MAX_WEDGED = int(os.environ.get("IV_REAPER_MAX_WEDGED", "3"))
REAPER_WEDGED_FRAC = float(os.environ.get("IV_REAPER_WEDGED_FRAC", "0.5"))
REAPER_WEDGE_S = float(os.environ.get("IV_REAPER_WEDGE_S", "1800"))
# DEFECT 108: after WE kill a browser tree, a worker still inside a Playwright
# call can stay blocked, and waiting the full 1800s to conclude that is pure
# waste. Measured on the s00 tail shard: the CPU limb killed all four browsers
# (10.7/12.3/10.3/14.5 cores), and 20 minutes later py-spy showed all four
# workers still blocked with ZERO chromium and ZERO driver processes alive --
# each holding a browser that no longer exists. They were spread across THREE
# call sites, which is why this is not one call's bug: `screenshot` (x2),
# `evaluate` under `dismiss_entry_gate`, and `detach` under `axtree.capture`.
# No verdict was written for any of them (only `checklist_gen.json`, and for two
# of them a `plan.json`), and the pass sat idle waiting out the 1800s window,
# after which the driver's max_attempts would re-run the same four tasks twice.
#
# NOT "a kill never raises". Probed directly (_probe_postkill_wedge.py): killing
# the tree under a SINGLE Playwright instance mid-`screenshot` raises in 5.0s
# with `Connection closed while reading from the driver`. So a kill normally
# does propagate, and what the tail hit needs a further ingredient -- four
# concurrent instances in one process is the obvious candidate, since each
# `sync_playwright` has its own dispatcher fiber and its own driver pipe.
# UNEXPLAINED, therefore, and deliberately treated as such: the reaper keys on
# the shape (blocked, no CPU, browser gone), never on a mechanism.
#
# The window is conditional on ATTRIBUTION, not on a global "recently killed"
# flag: only a thread that was already inside its call when we killed something
# gets the short window. Everything else keeps the full 1800s -- v0.51's rule
# that the reaper must not key on a theory of the cause still holds for what we
# cannot explain.
REAPER_WEDGE_AFTER_KILL_S = float(
    os.environ.get("IV_REAPER_WEDGE_AFTER_KILL_S", "120"))
# How much later than a kill a thread may be FIRST SEEN blocked and still be
# attributed to it. The reaper samples once a minute, so a thread already blocked
# at kill time can go unseen for nearly a full cycle; 90s covers that with slack.
# Over-attributing costs a restart 120s after a kill rather than 1800s, which for
# a lane that just lost a browser is nearly always right anyway.
KILL_ATTRIBUTION_GRACE_S = float(
    os.environ.get("IV_KILL_ATTRIBUTION_GRACE_S", "90"))
REAPER_EXIT_CODE = 7
# Set by `run_batch` so the reaper can compute a fraction. 0 means "unknown", in
# which case only the absolute threshold applies -- a caller that never tells us
# its pool size gets the old, conservative behaviour rather than a guess.
REAPER_POOL_SIZE = 0
# monotonic() of the most recent watchdog kill in this process, or None. Read by
# the reaper to choose its patience (DEFECT 108). Written by `_watch` threads and
# read by `_reap`; a plain float assignment is atomic enough for a value whose
# only use is "was there a kill recently", and a lock here could deadlock against
# a wedged thread.
_last_kill_at: float | None = None
_reaper_started = threading.Lock()
_reaper_on = False

# Call sites first seen to block forever with no timeout and no rejection. Both
# are `CDPSession.detach()` in a `finally`; see `_driver_pids` for why they cannot
# be interrupted from inside the process. Kept only as documentation of the two
# originals -- see DEFECT 104 for why they are NOT what the detector matches, and
# note that the `detach()` wedge WAS reproduced in isolation (unlike the
# `screenshot` one), so these two remain a real, distinct failure.
_UNBOUNDED_CALLS = (("axtree.py", "capture"), ("executor.py", "_cdp_screenshot"))

# The root frame of any thread currently inside a blocking Playwright sync call.
#
# This is NOT the obvious frame, and the obvious frame does not work. Playwright's
# sync API waits in `_sync_base.py:_sync`, but it waits by switching greenlets:
# `self._dispatcher_fiber.switch()` moves the OS thread onto the dispatcher fiber,
# and `sys._current_frames()` reports the fiber that is RUNNING. So during a
# blocking call the thread's visible stack is the asyncio event loop, rooted at
# `_context_manager.py:greenlet_main`; `_sync` and every one of OUR frames are
# parked on a fiber Python's own introspection cannot see. Measured directly:
#
#   idle in the with-block   leaf `<stdin>:work`        root `threading.py:_bootstrap`
#   inside wait_for_timeout  leaf `selectors.py:select` root `_context_manager.py:greenlet_main`
#   idle again afterwards    leaf `<stdin>:work`        root `threading.py:_bootstrap`
#
# DEFECT 104: this invisibility is why the previous detector never fired. It
# matched `_UNBOUNDED_CALLS` -- names of our OWN functions -- which are exactly
# the frames a blocking call hides. The reaper was armed, running, and sampling
# every 60s while three lanes sat with 7 of 8 slots wedged for 10 hours, and it
# could not have reported anything: the frames it looked for are unobservable in
# precisely the state it exists to detect. Its smoke test passed throughout,
# because a hand-built `def capture(ev): ev.wait()` frame has no greenlet under
# it. That test proved the matcher, never the signal. py-spy CAN see the hidden
# fibers (it walks greenlet state out-of-process), which is why the production
# stacks showed `_sync` -> `screenshot` -> `observe` while our in-process
# detector saw nothing.
#
# The MECHANISM of that wedge is still unexplained, and one plausible-sounding
# explanation is now refuted. "The watchdog kills the driver, so `screenshot`'s
# driver-side `timeout=8000` never applies" predicts a block; measured, every
# kill ordering RAISES within seconds -- driver-only, chrome-only, both, and with
# 6 concurrent drivers in one process while half are killed (`TargetClosedError`
# or `Connection closed while reading from the driver`, then the thread exits).
# So the reaper must not key on any theory of the cause. It keys on the SHAPE the
# production stacks actually showed: inside a page operation, making no progress,
# for half an hour. That holds for a wedge we cannot yet explain, and for the
# next one, which will have a different cause.
_PW_IN_CALL_ROOT = ("_context_manager.py", "greenlet_main")


def _thread_cpu_jiffies(native_id: int) -> int | None:
    """utime+stime for ONE thread, from /proc/self/task/<tid>/stat.

    Per-thread and not per-process: a lane with one working slot and seven wedged
    ones burns CPU, so a process-level reading cannot see the wedge. Fields 14 and
    15 (1-based) are utime/stime; the command name in field 2 can contain spaces
    and parentheses, so split after the LAST ')' rather than on whitespace.
    """
    try:
        with open(f"/proc/self/task/{native_id}/stat") as fh:
            rest = fh.read().rsplit(")", 1)[1].split()
        return int(rest[11]) + int(rest[12])
    except Exception:  # noqa: BLE001 - a vanished thread is not an error here
        return None


def _wedged_thread_ids(min_age_s: float, kill_at: float | None = None,
                       min_age_after_kill_s: float | None = None,
                       _state: dict = {}) -> list[int]:
    """Thread ids inside a Playwright call whose browser is gone, for ``min_age_s``.

    Detection is by Python stack plus the liveness of the thread's OWN browser
    tree, not by process topology in general, because the topology lies: after
    the watchdog kills a browser AND its driver, nothing external distinguishes
    the wedged thread from an idle slot.

    Two signals, and BOTH are required, because each alone is measurably useless:

    1. Root frame `_PW_IN_CALL_ROOT` — the thread is inside a blocking Playwright
       call (see that constant for why the call's own frames are invisible).
       Alone this flags every healthy worker: a busy slot is in a Playwright call
       almost all the time.
    2. Its own browser tree no longer exists (`_marker_by_thread`). A thread
       inside a page operation whose browser is gone cannot ever be completed by
       anything; a healthy worker's tree is alive by definition. Alone this flags
       a thread that has legitimately closed its browser and moved on, which is
       why the root frame is still required.

    Their conjunction, held for `min_age_s`, is the wedge: inside a page
    operation, against a browser that no longer exists.

    DEFECT 109 — why per-thread CPU is NOT the second signal, though it was.
    The old rule was "zero CPU across the window", and it made this detector
    unreachable for the very wedge it was written for. Measured on the live s00
    tail 568s after all four browsers were killed, all four wedged workers were
    accumulating CPU monotonically at ~0.25 cores each (514->2572 jiffies over
    100s). The clock-restart below (`jiff > first_j`) therefore dropped every one
    of them on every sighting. The cause is that Playwright's sync bridge SPINS
    rather than blocks — `while not task.done(): self._dispatcher_fiber.switch()`
    — so a call awaiting a reply that will never come burns CPU indefinitely. The
    original "+0 jiffies" measurement came from `wait_for_timeout`, which parks in
    `selectors.select`; that is the one Playwright call that genuinely idles, and
    generalizing from it inverted the limb's meaning for every other call.

    CPU is still recorded per thread, but it does NOT gate anything and must not:
    a spinning wedge always trips a "did it burn CPU" test, which is precisely how
    this detector came to be unreachable. The number is kept because it is the
    cheapest evidence that distinguishes this failure from an idle one when
    reading a reaper line after the fact.

    ``kill_at`` + ``min_age_after_kill_s`` shorten the requirement for the
    threads a watchdog kill can EXPLAIN (DEFECT 108), and only those. A thread
    qualifies when it was already inside its call when we killed the browser
    (``first_t <= kill_at + grace``) and the kill is at least
    ``min_age_after_kill_s`` in the past. Both halves matter:

      * Attribution is per-THREAD, not per-process. A process-wide "a kill
        happened recently, so use the short window" also shortens the window for
        threads that entered a call afterwards, on browsers still alive — which
        is the unexplained case that must keep its full patience.
      * ``kill_at`` is never cleared, so a process-wide flag latches ON forever
        after the first kill; keying on `first_t` makes a stale stamp harmless,
        since it can only match a thread that has been wedged since before it.

    The grace is deliberately generous (the reaper samples once a minute, so a
    thread blocked at kill time may not be first SEEN until up to a minute
    later). Over-attributing costs a lane restart 120s after a kill instead of
    1800s; for a lane that just lost a browser that is nearly always the right
    call anyway.

    State is a mutable default (one reaper thread, one call site) mapping thread
    id -> (first sighting, jiffies then). A thread that leaves the call, or whose
    browser tree is alive again, is dropped from the state and must start its
    clock over — so the age is time spent continuously wedged, not merely two
    sightings that happen to match.
    """
    import sys
    now = time.monotonic()
    # native_id per Python thread id: /proc keys on the OS tid, frames on the
    # Python one, and only live Thread objects carry both.
    natives = {t.ident: t.native_id for t in threading.enumerate()
               if t.ident is not None and t.native_id is not None}
    # Forget markers belonging to threads that no longer exist. Python thread
    # idents are RECYCLED, so a dead worker's entry is not merely garbage: the
    # next thread handed that ident would inherit a marker pointing at a browser
    # that is already gone -- i.e. precisely the shape this function reports on --
    # before it ever reaches its own `launch_guarded`. `release_marker` covers the
    # clean paths; this covers a thread that died holding one.
    for dead in [tid for tid in _marker_by_thread if tid not in natives]:
        _marker_by_thread.pop(dead, None)
    # ONE process scan for the whole sample. A psutil failure here must never
    # reap a lane, so an exception means "report nothing this cycle": the state
    # dict is left untouched, ages keep accruing, and the next cycle decides.
    try:
        live = _live_markers()
    except Exception:  # noqa: BLE001 - see above
        return []
    seen: dict[int, tuple[float, int]] = {}
    out = []
    for tid, frame in list(sys._current_frames().items()):
        root = frame
        while root.f_back is not None:
            root = root.f_back
        if (os.path.basename(root.f_code.co_filename), root.f_code.co_name) \
                != _PW_IN_CALL_ROOT:
            continue
        # The discriminating limb (DEFECT 109): is THIS thread's own browser tree
        # still alive? A thread we have no marker for is skipped rather than
        # reported -- it has not reached `launch_guarded` yet, or it released its
        # marker at a clean close, so the absence is ignorance rather than
        # evidence. Failing closed here would reap every worker during startup.
        marker = _marker_by_thread.get(tid)
        if marker is None or marker in live:
            continue
        native = natives.get(tid)
        if native is None:
            continue
        jiff = _thread_cpu_jiffies(native)
        if jiff is None:
            continue
        first_t, first_j = _state.get(tid, (now, jiff))
        seen[tid] = (first_t, first_j)
        need = min_age_s
        if (kill_at is not None and min_age_after_kill_s is not None
                and first_t <= kill_at + KILL_ATTRIBUTION_GRACE_S
                and now - kill_at >= min_age_after_kill_s):
            need = min(need, min_age_after_kill_s)
        if now - first_t >= need:
            out.append(tid)
    _state.clear()
    _state.update(seen)
    return out


def _should_give_up(n_wedged: int) -> bool:
    """Has this process lost enough slots that a restart beats continuing?

    Two thresholds, whichever fires first:

      * an ABSOLUTE count (``REAPER_MAX_WEDGED``) -- right for a big pool, where
        3 dead slots out of 12 is a real pattern and 25% of the work.
      * a FRACTION of the pool (``REAPER_WEDGED_FRAC``) -- right for a small one.

    DEFECT 106: the absolute count alone is unreachable on a small pool, and
    defect 105's fix made small pools normal (a tail shard with 4 remaining
    instances runs 4 slots). Measured on the s00 tail shard: 2 of 4 slots wedged
    with the greenlet root and +0 jiffies over 25s, 50% of the pool gone, and the
    reaper could not fire because 2 < 3. Both wedged tasks had stopped right
    after `checklist_gen.json` and produced nothing for 13 minutes while the
    process, the log, and the shard checkpoint all reported health.

    The floor is deliberate: at ``REAPER_MAX_WEDGED``-and-below pool sizes the
    fraction would trip on a single slot, and a 1-of-2 wedge is a coin flip
    rather than a pattern -- restarting on it risks a loop that redoes the same
    in-flight task forever. So a pool must have at least 3 slots for the
    fractional rule to apply, and 1 wedged slot never suffices.
    """
    if n_wedged >= REAPER_MAX_WEDGED:
        return True
    pool = REAPER_POOL_SIZE
    if pool >= 3 and n_wedged >= 2:
        return n_wedged >= pool * REAPER_WEDGED_FRAC
    return False


def _arm_reaper() -> None:
    """Start the one-per-process thread that exits a lane that has bled slots.

    This is a LAST resort and deliberately blunt: it cannot un-wedge a thread
    (nothing in-process can — that is the whole point of `_driver_pids`), so the
    only lever left is to end the process and let the supervisor start a clean
    one. That is cheap here and only here, because resume is verdict-existence
    based: a restarted lane redoes only the tasks in flight at exit, and every
    verdict already written is untouched.

    Exiting is strictly better than not exiting. A lane that has lost most of
    its slots is not failing — it is silently doing a fraction of the work while
    every external signal (process alive, no traceback, no `exited rc=`) reports
    health. That is the exact failure this sweep already paid for once.

    `os._exit` rather than `sys.exit`: the wedged threads are non-daemon pool
    workers blocked in a call that never returns, so a clean interpreter
    shutdown would join them and hang forever — the shutdown would inherit the
    very defect it is escaping. Verdicts are written atomically as they finish,
    so there is no buffered state to flush."""
    global _reaper_on
    with _reaper_started:
        if _reaper_on:
            return
        _reaper_on = True

    def _reap() -> None:
        while True:
            time.sleep(60)
            try:
                # DEFECT 108: patience is per-thread, keyed on whether a
                # watchdog kill in this process can EXPLAIN that thread's block.
                # A thread already inside its call when we killed a browser is
                # holding a dead browser and no further waiting changes that. A
                # thread that entered a call afterwards gets the full window --
                # v0.51's rule that the reaper must not key on a theory of the
                # cause still holds for what we cannot explain. `_last_kill_at`
                # is never cleared, so this must NOT be a process-wide flag:
                # that would latch on after the first kill and quietly shorten
                # the window for every unexplained wedge thereafter.
                kill_at = _last_kill_at
                wedged = _wedged_thread_ids(
                    REAPER_WEDGE_S, kill_at=kill_at,
                    min_age_after_kill_s=REAPER_WEDGE_AFTER_KILL_S)
                if not _should_give_up(len(wedged)):
                    continue
                pool = REAPER_POOL_SIZE or -1
                why = ("" if kill_at is None else
                       f" (a watchdog kill {time.monotonic() - kill_at:.0f}s ago "
                       f"may explain some of them)")
                # flush=True: os._exit skips atexit and never drains buffers.
                print(f"[reaper] {len(wedged)} of {pool} worker slot(s) in a "
                      f"Playwright call against a browser that no longer exists, "
                      f"for >{REAPER_WEDGE_AFTER_KILL_S:.0f}-{REAPER_WEDGE_S:.0f}s"
                      f"{why} "
                      f"(tids {wedged}); exiting rc={REAPER_EXIT_CODE} so the "
                      f"supervisor restarts this lane with a clean pool. "
                      f"Verdicts already written are unaffected; only in-flight "
                      f"tasks are redone.", flush=True)
                os._exit(REAPER_EXIT_CODE)
            except Exception:
                continue

    threading.Thread(target=_reap, daemon=True, name="iv-slot-reaper").start()


def launch_guarded(chromium: Any, **kwargs: Any) -> Any:
    """``chromium.launch`` + an armed watchdog, as one call.

    Use this instead of ``chromium.launch`` at EVERY launch site. Arming used to
    be a second call the caller made afterwards, and that shape hid a total
    failure for an entire sweep: it read ``browser.process.pid``, but ``.process``
    exists only on the Node Playwright API — the Python ``Browser`` has no such
    attribute, so the best-effort ``except`` swallowed an ``AttributeError`` on
    every launch and the watchdog never once armed in production. (The smoke test
    passed throughout, because its fake browser supplied ``.process``.) The
    observed cost: b0810 tasks running 4165s against a 1200s limit with zero
    kills logged.

    Since Python Playwright never exposes the pid, we plant a unique marker
    switch on the command line and find the browser process by cmdline; Chromium
    ignores switches it does not recognize. The marker lands on the browser
    process only — children carry `--type=...` instead — so the watchdog walks
    psutil children from there to get the renderers, which is what the RSS check
    needs, because a leaking artifact's memory lives in the renderer.

    Any extra ``args`` passed in are appended to the marker; pass the SwiftShader
    set via ``args=chromium_launch_args()`` as before."""
    marker = f"{_MARKER_PREFIX}{os.getpid()}-{next(_marker_seq)}"
    kwargs["args"] = list(kwargs.get("args") or []) + [marker]
    # Recorded BEFORE the launch, not after. `chromium.launch` is itself a
    # blocking Playwright call, so a thread sits in `_PW_IN_CALL_ROOT` for its
    # duration; if the map still held the PREVIOUS task's marker, that tree is
    # already gone and the reaper would see a healthy launch as a wedge.
    # Publishing the new marker first makes the worst case a tree that has not
    # appeared yet, which the age requirement absorbs.
    _marker_by_thread[threading.get_ident()] = marker
    # v0.70 — stamp this tree's 3D flag from the launching thread's pending flag.
    # Signature unchanged, so every existing caller (which never sets a pending
    # flag) defaults to False = today's base ceiling.
    _heavy_by_marker[marker] = _heavy_pending_by_thread.get(threading.get_ident(), False)
    browser = chromium.launch(**kwargs)
    _last_marker.append(marker)
    _arm(marker)
    _arm_reaper()
    return browser


# The marker of every browser launched by this process, in launch order. Exists
# so a test can ask "is that tree still alive?" without re-deriving the marker,
# which is generated inside `launch_guarded` and otherwise never escapes. Kept as
# a list rather than a single value because a leak here would be silent: growth
# is visible, an overwrite is not.
_last_marker: list[str] = []

# Thread ident -> the marker of the browser that thread most recently launched.
#
# DEFECT 109: this is the third limb of the wedge shape, and the only one that
# actually discriminates. The reaper's docstring has claimed since v0.51 that it
# keys on "blocked, no CPU, browser gone", but `browser gone` was never
# implemented -- and the other two limbs cannot see the wedge that motivated
# them. Measured on the live s00 tail (pid 2320695, 568s after all four browsers
# were killed): every one of the four wedged workers was accumulating CPU
# MONOTONICALLY, ~500 jiffies per 20s, i.e. 0.25 cores each, steady across 100s:
#
#     t=20s   514   476   506   508
#     t=100s 2572  2456  2452  2533
#
# `_wedged_thread_ids` restarts a thread's clock whenever `jiff > first_j`, so
# each of those threads was dropped on every single sighting and could never age
# past `min_age_s`. The reaper was UNREACHABLE for this wedge at any window --
# which is why defect 108's 1800s->120s change produced no `[reaper]` line: the
# window was never the binding constraint.
#
# The reason is in Playwright's own sync bridge (`_sync_base.py:_sync`):
#
#     while not task.done():
#         self._dispatcher_fiber.switch()
#
# That is a SPIN, not a block. A greenlet switch per iteration is real Python
# execution, so a thread waiting on a reply that will never come burns CPU
# forever. "Zero CPU" was measured on `wait_for_timeout` -- which parks in
# `selectors.select` and genuinely idles -- and generalized to all blocking
# calls. It does not generalize: the wedge and the idle case have opposite CPU
# signatures, so the limb that was supposed to be conservative silently inverted.
#
# `browser gone` has neither weakness. It is not a rate, so no threshold tuning
# and no spin/idle distinction; and it is the one fact that is unambiguously
# terminal -- a thread inside a page call whose browser tree no longer exists
# cannot ever be completed by anything. The map is per-thread because each worker
# owns its own `sync_playwright` and its own browser, so "my tree" is exactly the
# tree whose absence condemns me.
_marker_by_thread: dict[int, str] = {}


def release_marker() -> None:
    """Forget this thread's browser marker, at a deliberate close.

    Without this the map keeps pointing at a tree we ourselves just shut down, so
    a thread between tasks — inside `context.close()`, `browser.close()`, or the
    next `launch` — looks exactly like a wedge: in a Playwright call, tree gone.
    The age requirement makes that harmless in practice (a close does not take
    120s), but "harmless because it is slow" is not a property to rely on when one
    explicit call removes the ambiguity entirely.
    """
    m = _marker_by_thread.pop(threading.get_ident(), None)
    if m is not None:
        # The 3D flag is keyed on the marker; drop it with the marker so the map
        # cannot grow unbounded across a worker's lifetime of tasks (v0.70).
        _heavy_by_marker.pop(m, None)


# ---------------------------------------------------------------------------
# v0.70 — per-task 3D uplift carry mechanism + detector.
#
# `_watch` runs on the watchdog daemon thread and closes over the launching
# tree's `marker`, so a `threading.local` set by the worker would be invisible to
# it. A plain module dict keyed by marker is the direct, race-safe carrier (a
# str-keyed get/set is atomic under the GIL), mirroring the existing
# `_marker_by_thread` / `_last_marker` style. `_watch` reads
# `_heavy_by_marker.get(marker, False)` fresh on each tick, so a per-tree ceiling
# takes effect on the next 10s tick after the flag is set — the same seam the CPU
# window already relies on.
#
# The flag is stamped at LAUNCH from a per-thread "pending" flag rather than
# passed as a launch_guarded argument, because launch_guarded's signature must
# stay unchanged (every existing caller then defaults to non-heavy = today's
# behaviour) AND because the throwaway AX-capture browser is launched deep inside
# `capture_axtree` on a load thread we do not thread arguments through. The loader
# sets the pending flag on the load thread around that launch; the reused
# trajectory browser is (re)flagged per task via `set_tree_heavy` on the worker
# thread (see run.py).
_heavy_by_marker: dict[str, bool] = {}
_heavy_pending_by_thread: dict[int, bool] = {}

# A detected-3D artifact reads a higher watchdog ceiling. STRONG signals only —
# the tokens below are what actually costs sustained CPU/RSS in a headless
# SwiftShader render. A bare <canvas>, requestAnimationFrame alone, or a 2D
# context are deliberately NOT here: the 2D-canvas long tail must keep today's
# ceiling, or the uplift stops being a narrow relaxation.
_HEAVY_3D_RE = re.compile(
    r"""(?ix)                       # case-insensitive, verbose
    getContext\s*\(\s*['"](?:webgl|webgl2|experimental-webgl|webgpu)['"]  # GL context
  | three\.module | three\.min | three\.js | /three@                     # three.js bundle
  | THREE\. | new\s+THREE                                                 # three.js API
  | babylon | BABYLON\.                                                   # Babylon.js
  | a-frame | <a-scene | aframe                                           # A-Frame
  | playcanvas | \bregl\b | gl-matrix                                     # other engines
  | WebGL2?RenderingContext                                               # GL context type
  | \.drawArrays\b | \.drawElements\b                                     # GL draw calls
  | createShader\b | shaderSource\b                                       # GL shader setup
    """
)

# Cap the per-artifact scan: a 3D signal almost always appears in the <head> or
# early <script>, and reading a multi-MB minified bundle in full on the load
# thread would tax the very sweep this feature speeds up. A signal past the cap
# just yields non-3D = base ceiling — a strict-relaxation-safe miss (a heavy tree
# treated as plain is caught earlier, never a plain tree treated as heavy).
_HEAVY_SCAN_BYTES = 512 * 1024


def detect_heavy_3d(html: str, code: str = "") -> bool:
    """True iff a static scan of the artifact HTML (and any gathered bundle JS)
    finds a STRONG 3D/WebGL signal. `code` covers engines that live in a separate
    `<script src>` bundle rather than inline in the HTML."""
    if html and _HEAVY_3D_RE.search(html):
        return True
    if code and _HEAVY_3D_RE.search(code):
        return True
    return False


def detect_heavy_3d_path(path: str | os.PathLike) -> bool:
    """`detect_heavy_3d` over the first `_HEAVY_SCAN_BYTES` of the file at ``path``.

    Best-effort: an unreadable or missing entry yields False (base ceiling), never
    an exception — a detector that raised would break loading a task it was only
    meant to characterize."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
            return detect_heavy_3d(fh.read(_HEAVY_SCAN_BYTES))
    except Exception:  # noqa: BLE001 - detection must never break a load
        return False


def set_tree_heavy(heavy: bool) -> None:
    """Flag (or un-flag) THIS thread's most-recently-launched browser tree heavy.

    Used per task on the worker thread for the reused trajectory browser: the
    browser is launched once and reused across tasks, so its ceiling is updated
    here at the start of each task and restored at the end. A no-op if this thread
    has no live marker (nothing to flag)."""
    m = _marker_by_thread.get(threading.get_ident())
    if m is not None:
        _heavy_by_marker[m] = heavy


def set_heavy_pending(heavy: bool) -> None:
    """Mark the NEXT guarded launch on this thread heavy (or not).

    For the throwaway AX-capture browser, which launch_guarded creates deep inside
    `capture_axtree` on the load thread — we cannot pass it an argument, so the
    loader stamps intent here and `launch_guarded` reads it when it mints the
    marker."""
    _heavy_pending_by_thread[threading.get_ident()] = heavy


def clear_heavy_pending() -> None:
    """Clear this thread's pending-heavy flag (call in a finally after the launch)."""
    _heavy_pending_by_thread.pop(threading.get_ident(), None)


def _live_markers() -> set[str]:
    """Every watchdog marker present on a live process, in ONE process scan.

    One scan, not one per candidate thread: `_find_marked_pids` walks all of
    /proc, and calling it per wedged candidate made the reaper's cost scale with
    pool size for no reason. At 12 workers that was a dozen full scans a minute.

    Self and our own ancestors are excluded for the same reason as in
    `_find_marked_pids`: a shell or driver whose command line happens to carry the
    string would otherwise register as a live browser and mask a real wedge.
    """
    import psutil
    try:
        me = psutil.Process()
        forbidden = {me.pid} | {a.pid for a in me.parents()}
    except Exception:
        forbidden = set()
    out: set[str] = set()
    for pr in psutil.process_iter(["pid", "cmdline"]):
        try:
            if pr.info["pid"] in forbidden:
                continue
            for a in (pr.info.get("cmdline") or []):
                if a.startswith(_MARKER_PREFIX):
                    out.add(a)
        except Exception:  # process died mid-iteration / access denied
            continue
    return out


def _find_marked_pids_for_test() -> list[int]:
    """PIDs of the most recently launched guarded browser tree, or []."""
    if not _last_marker:
        return []
    return _find_marked_pids(_last_marker[-1])


def _arm(marker: str) -> None:
    """Start the watchdog thread for the process tree carrying ``marker``.

    Best-effort throughout: a watchdog that raises must never be what breaks a
    run, and the limits sit far above any legitimate artifact so a healthy task
    is never touched. Daemon thread so it can never hold up interpreter exit."""
    try:
        import psutil  # noqa: F401  (fail fast here if it's missing)
    except Exception:
        return

    def _watch() -> None:
        import psutil
        deadline = time.monotonic() + BROWSER_HARD_LIMIT_S
        # (monotonic, summed cpu_times across the tree) at the start of the CPU
        # window. Averaging over a window rather than sampling `cpu_percent` is
        # the point: a runaway is sustained, and scene init is not.
        cpu_mark: tuple[float, float] | None = None
        while True:
            time.sleep(10)
            try:
                pids = _find_marked_pids(marker)
                if not pids:
                    return  # browser exited (normally, or we already killed it)
                procs = []
                rss = 0
                cpu = 0.0
                for pid in pids:
                    try:
                        pr = psutil.Process(pid)
                        rss += pr.memory_info().rss
                        t = pr.cpu_times()
                        cpu += t.user + t.system
                        procs.append(pr)
                    except Exception:
                        continue
                # v0.70 — read this tree's 3D flag fresh each tick and pick the
                # ceilings by `max(base, uplift)`. A strict relaxation: a non-3D
                # tree (heavy=False) uses the base limits bit-for-bit; the time
                # limb is never uplifted.
                heavy = _heavy_by_marker.get(marker, False)
                rss_ceiling_mb = (max(BROWSER_RSS_LIMIT_MB, BROWSER_RSS_LIMIT_3D_MB)
                                  if heavy else BROWSER_RSS_LIMIT_MB)
                cpu_ceiling = (max(BROWSER_CPU_LIMIT_CORES, BROWSER_CPU_LIMIT_3D_CORES)
                               if heavy else BROWSER_CPU_LIMIT_CORES)
                over_mem = rss > rss_ceiling_mb * 1024 * 1024
                over_time = time.monotonic() > deadline
                # Sustained CPU across the whole browser tree, in cores. The
                # window restarts every time it closes, so a burst followed by
                # calm never accumulates into a kill (DEFECT 107).
                now = time.monotonic()
                over_cpu = False
                cores = 0.0
                if cpu_mark is None:
                    cpu_mark = (now, cpu)
                elif now - cpu_mark[0] >= BROWSER_CPU_WINDOW_S:
                    cores = (cpu - cpu_mark[1]) / (now - cpu_mark[0])
                    over_cpu = cores > cpu_ceiling
                    cpu_mark = (now, cpu)
                if not (over_mem or over_time or over_cpu):
                    continue
                why = ("rss" if over_mem else
                       "time" if over_time else
                       f"cpu {cores:.1f} cores sustained "
                       f"{BROWSER_CPU_WINDOW_S:.0f}s")
                # The driver must die WITH the browser. A chrome-only kill leaves
                # any in-flight CDPSession.detach() blocked forever (see
                # _driver_pids), which converts one kill into one permanently
                # lost worker slot. Killed after the browser so a driver that
                # exits on its own is simply not there to kill.
                drivers = []
                try:
                    drivers = _driver_pids(pids)
                except Exception:
                    pass
                print(f"[watchdog] killing browser tree {pids} "
                      f"+ driver(s) {drivers} "
                      f"({why}: {rss / 1e9:.1f}GB, "
                      f"{BROWSER_HARD_LIMIT_S:.0f}s limit, "
                      f"{cpu_ceiling:.1f}-core limit, "
                      f"{rss_ceiling_mb / 1024:.1f}GB rss limit, "
                      f"heavy_3d={heavy})", flush=True)
                # Stamped BEFORE the kill, so the reaper's shortened patience
                # covers threads that were already blocked when we pulled the
                # browser out from under them (DEFECT 108).
                global _last_kill_at
                _last_kill_at = time.monotonic()
                for pr in procs:
                    try:
                        pr.kill()
                    except Exception:
                        pass
                for dpid in drivers:
                    try:
                        psutil.Process(dpid).kill()
                    except Exception:
                        pass
                return
            except Exception:
                return

    threading.Thread(target=_watch, daemon=True,
                     name="iv-browser-watchdog").start()
