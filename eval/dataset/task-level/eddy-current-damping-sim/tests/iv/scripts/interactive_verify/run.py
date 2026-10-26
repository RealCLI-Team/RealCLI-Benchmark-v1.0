#!/usr/bin/env python3
"""CLI + batch orchestration for the 3-stage interactive verifier.

Reads a rolling run's artifacts + the subset's embedded checklists, runs each
task through the pipeline with a small worker pool (each worker owns its own
Playwright browser), resumes by skipping tasks with a clean verdict, and writes a
sidecar tree under traj_rolling_system/verify_runs/interactive_<ts>/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from scripts.interactive_verify import browser_env, loader, models, pipeline, planner
from scripts.interactive_verify.loader import TaskInputMissing
from scripts.interactive_verify._bootstrap import ensure_service_path
from scripts.interactive_verify.browser_env import (
    chromium_launch_args, launch_guarded, release_marker)

ensure_service_path()
from service.rolling import paths as rolling_paths  # noqa: E402


def resolve_out_dir(explicit: str | None, ts: str) -> Path:
    out = Path(explicit) if explicit else rolling_paths.ROLLING_ROOT / "verify_runs" / f"interactive_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def already_done(out_dir: Path, task_id: str) -> bool:
    """Resume predicate: a task counts as done only if it has a verdict.json with
    NO error. An error verdict (or unreadable/absent file) is retried on the next
    run — so a `--force`-less rerun cheaply skips clean results and reattempts the
    failures."""
    vp = Path(out_dir) / task_id / "verdict.json"
    if not vp.exists():
        return False
    try:
        v = json.loads(vp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return not str(v.get("error") or "").strip()


# Wall-clock ceiling on the LOAD phase (extract + checklist-gen), in seconds.
# The verify stages have step_timeout + browser watchdog + slot reaper; the load
# phase had NONE, so a CPU-STARVED trivial extract could hold its worker slot for
# tens of minutes (measured: a 3.7MB/2435-member tar whose getmembers() is 0.23s
# idle stretched to 2523s under oversubscription) and burn the WHOLE 1800s driver
# stall budget, which then group-kills the entire shard. A generous ceiling —
# ~1800x the 0.33s median load, still under the 1800s stall-timeout — lets one
# starved slot yield to a retry instead of taking the shard down with it. Env
# override so a heavy cohort can raise it without a code change.
LOAD_TIMEOUT_S = float(os.environ.get("IV_LOAD_TIMEOUT_S", "600"))
# NOTE (DEFECT 117): there is deliberately NO wall-clock bound on finalize() here.
# v0.64 added one (`_finalize_with_timeout`, IV_FINALIZE_TIMEOUT_S) that ran the
# close on a DAEMON thread — but Playwright's sync API is greenlet-thread-affine:
# context/browser/page and p.stop() can only be called from the thread that ran
# sync_playwright().start(). Calling close() off-thread throws "Cannot switch to a
# different thread" immediately (silently swallowed → nothing closed) AND leaves
# the worker's asyncio loop un-torn-down, so the next task's start() crashes the
# whole shard ("Sync API inside the asyncio loop", rc=1). So finalize MUST run
# inline on the worker thread. The rare finalize-wedge that v0.64 targeted (an
# UNEXPLAINED multi-instance tail; see browser_env.py:283-296) is netted instead
# by the reaper + the out-of-process 1800s stall-kill, and by writing the verdict
# BEFORE finalize (below) so a wedged close can never lose a computed verdict.


class LoadTimeout(Exception):
    """The load phase exceeded LOAD_TIMEOUT_S. Handled exactly like
    TaskInputMissing (leave the task for retry, write NO verdict) — a starved
    load is an environment fault, not a property of the artifact, so freezing a
    0.0 here would permanently mislabel a good artifact that a less-loaded pass
    would verify fine."""


def _load_with_timeout(load_fn, tid: str, breakdown: dict[str, float],
                       timeout_s: float):
    """Run ``load_fn`` on a daemon thread and join with a deadline.

    A ThreadPoolExecutor worker cannot use signal.alarm (not the main thread),
    so we isolate the (possibly starved) load in its own thread and abandon it
    on timeout. The abandoned thread is a daemon: it cannot block interpreter
    exit, and its scratch tree is reclaimed by the next census/`--force`-less
    resume, so leaking it costs disk transiently but never correctness. Re-raise
    the loader's own exception in THIS thread so the caller's existing
    TaskInputMissing / generic-error branches keep working unchanged."""
    result: dict[str, Any] = {}

    def _work():
        try:
            try:
                result["task"] = load_fn(tid, breakdown)
            except TypeError:  # a load_fn that predates the timings kwarg
                result["task"] = load_fn(tid)
        except BaseException as exc:  # noqa: BLE001 - carried across the thread
            result["exc"] = exc

    th = threading.Thread(target=_work, name=f"load-{tid}", daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        raise LoadTimeout(f"load exceeded {timeout_s:.0f}s (CPU-starved slot)")
    if "exc" in result:
        raise result["exc"]
    return result["task"]


def _summary_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # input_missing / load_timeout rows are excluded from `scored` alongside skips:
    # no verdict was written for them, so counting them as errors would
    # double-report the fault (once as an error, once as input_missing/load_timeout)
    # and drag mean_score's denominator. A load_timeout is a starved-slot fault the
    # next pass retries, exactly like a missing input.
    scored = [r for r in rows if not r.get("skipped")
              and not r.get("input_missing") and not r.get("load_timeout")]
    errs = [r for r in scored if r.get("error")]
    renderable = [r for r in scored if r.get("renderable")]
    # Build defects are advisory (score untouched) but load-bearing for triage:
    # count how many artifacts rendered-but-broken (forced-visible overlay /
    # unreachable CDN) so a batch summary flags them at a glance.
    defective = [r for r in scored if r.get("build_defect")]
    # Plans carrying a defect signature (capture-on-a-wait / out-of-viewport /
    # duplicate probe). Advisory, but the count is the run's regression detector:
    # these produce wrong verdicts rather than errors.
    plan_def = [r for r in scored if r.get("plan_defects")]
    # Tasks whose action loop hit EXEC_BUDGET_S and was cut off mid-plan. This is
    # NOT advisory for a gate round: a truncated task's determinacy and score are
    # a measurement of the machine, not the artifact. One round ran under 93
    # orphan Chromium processes, truncated 4 of 6 tasks, and produced a "1.000"
    # resting on 2 of 12 items. Surfacing the count here is what makes that
    # readable from the summary instead of by hand-diffing trajectories.
    truncated = [r for r in scored if r.get("exec_truncated")]
    # Tasks whose input was not on disk. These wrote NO verdict (see _one), so
    # they are still owed work — but without a count here a vanished scratch tree
    # reads as a quiet, successful run. A non-zero value means the run is
    # INCOMPLETE, not that these artifacts are bad.
    missing = [r for r in rows if r.get("input_missing")]
    # Same "still owed, wrote no verdict" class as missing, but the fault is a
    # CPU-starved load rather than an absent input. Counted separately so the
    # summary distinguishes "input vanished" from "slot was starved".
    load_to = [r for r in rows if r.get("load_timeout")]
    # LEGACY field (pre-DEFECT-117). v0.64 set finalize_timeout when it abandoned
    # a close on a daemon thread; that path was removed in v0.66 because Playwright
    # is thread-affine and cannot be closed off-thread. Kept only so old verdicts
    # on disk that carry the field still summarise; new runs never set it.
    fin_to = [r for r in scored if r.get("finalize_timeout")]
    scores = [r.get("score", 0.0) for r in scored if not r.get("error")]
    mean = round(sum(scores) / len(scores), 4) if scores else 0.0
    # Total checklist-gen token/cost across the run (per-task gen_cost on verdicts).
    gen_tokens = sum((r.get("gen_cost") or {}).get("total_tokens", 0) for r in rows)
    gen_cost = round(sum((r.get("gen_cost") or {}).get("cost_usd", 0.0) for r in rows), 6)
    return {"count": len(rows), "scored": len(scored), "errors": len(errs),
            "renderable": len(renderable), "build_defects": len(defective),
            "plan_defects": len(plan_def),
            "exec_truncated": len(truncated),
            "input_missing": len(missing),
            "load_timeout": len(load_to),
            "finalize_timeout": len(fin_to),
            "mean_score": mean,
            "gen_tokens": gen_tokens, "gen_cost_usd": gen_cost}


def write_summary(out_dir: Path, rows: list[dict[str, Any]]) -> None:
    agg = _summary_rows(rows)
    payload = {**agg, "tasks": [
        {"task_id": r.get("task_id"), "score": r.get("score"),
         "renderable": r.get("renderable"), "satisfied": r.get("satisfied"),
         "determinate": r.get("determinate"), "error": r.get("error"),
         "build_defect": r.get("build_defect", False),
         "exec_truncated": r.get("exec_truncated", False),
         "build_defects": r.get("build_defects", []),
         "skipped": r.get("skipped", False)} for r in rows]}
    pipeline.write_json_atomic(Path(out_dir) / "summary.json", payload)
    lines = ["# Interactive verification summary",
             f"\n- tasks: {agg['count']}  scored: {agg['scored']}  "
             f"errors: {agg['errors']}  renderable: {agg['renderable']}  "
             f"build-defects: {agg.get('build_defects', 0)}  "
             f"plan-defects: {agg.get('plan_defects', 0)}  "
             f"exec-truncated: {agg.get('exec_truncated', 0)}",
             f"- mean score (non-error): {agg['mean_score']}",
             f"- checklist-gen: {agg.get('gen_tokens', 0)} tokens  "
             f"${agg.get('gen_cost_usd', 0.0)}",
             "\n| task | score | renderable | sat/det | defect | trunc | error |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        # kinds of any detected build defect (empty when clean)
        dkinds = ",".join(sorted({d.get("kind", "") for d in (r.get("build_defects") or [])}))
        lines.append(f"| {r.get('task_id')} | {r.get('score')} | {r.get('renderable')} "
                     f"| {r.get('satisfied')}/{r.get('determinate')} | {dkinds} "
                     f"| {'TRUNC' if r.get('exec_truncated') else ''} "
                     f"| {r.get('error') or ''} |")
    (Path(out_dir) / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_batch(ids: list[str], run_id: str | None, source_index: dict[str, dict[str, Any]] | None,
              out_dir: Path, vlm_call: Callable, text_call: Callable, *,
              workers: int, page_factory: Callable, force: bool,
              max_frames: int, max_steps: int, step_timeout_s: int,
              aesthetics_call: Callable | None = None,
              planner_call: Callable | None = None,
              load_fn: Callable[[str], dict[str, Any]] | None = None,
              plan_override_fn: Callable[[str], dict[str, Any] | None] | None = None,
              ) -> list[dict[str, Any]]:
    out_dir = Path(out_dir)
    # load_fn is the ONLY thing that differs between the rolling-run entry point
    # (run.main, below) and the Harbor entry point (run_harbor.py): the former
    # reads a built workspace + embedded checklist, the latter extracts a tar and
    # generates a checklist. Everything downstream is identical, so we inject it.
    if load_fn is None:
        def load_fn(tid: str, timings: dict[str, float] | None = None) -> dict[str, Any]:  # rolling default
            return loader.load_task(run_id, tid, source_index)
    # Each worker thread gets its OWN browser page: the Playwright sync API is not
    # thread-safe across a shared browser, so we stash one per-thread via
    # threading.local and build it lazily on first use (page_factory()).
    local = threading.local()

    def _page_and_finalize():
        made = page_factory()
        if isinstance(made, tuple):
            return made
        return made, (lambda _out: None)

    def _worker_page():
        if not hasattr(local, "pf"):
            local.pf = _page_and_finalize()
        return local.pf

    lock = threading.Lock()
    rows: list[dict[str, Any]] = [None] * len(ids)  # type: ignore

    def _release_workspace(task: dict[str, Any] | None) -> None:
        """Delete a task's EXTRACTED workspace once its verdict is on disk.

        Harbor tasks are extracted per task into ``out_dir/_work/<id>`` and nothing
        downstream reads them again — the verdict, frames, trajectory and report are
        all written already. Left in place they are the sweep's binding resource
        limit rather than a tidiness matter: b0811 measured 14.1 MB/task, which over
        17,833 instances is ~245 GB of extracted node_modules against 194 GB free.
        The sweep would have run out of disk around shard 35 of 72 — after ~3 days
        of compute — and every later shard would then fail at extract.

        The guard is what makes this safe to do unconditionally. Only a path INSIDE
        ``out_dir`` is removed, which is exactly the harbor/rolling discriminator:
        the harbor loader extracts under ``out_dir/_work``, while the rolling loader
        (loader.py) points ``workspace`` at the run's OWN built artifact under
        traj_rolling_system — production data that must never be deleted. Anything
        outside ``out_dir``, missing, or unparseable is left untouched."""
        ws = (task or {}).get("workspace")
        if not ws:
            return
        try:
            p = Path(ws).resolve()
            if not p.is_dir() or not p.is_relative_to(out_dir.resolve()):
                return  # rolling artifact, or already gone — never ours to delete
            shutil.rmtree(p, ignore_errors=True)
        except Exception:  # noqa: BLE001 - reclaiming disk must never fail a task
            pass

    def _one(i_tid):
        # The unit of work for one task, run on a pool thread: resume-check ->
        # load -> verify (Stages 1-3b) -> fold in load/finalize timings -> persist.
        i, tid = i_tid
        if not force and already_done(out_dir, tid):
            return i, {"task_id": tid, "method": "interactive", "skipped": True}
        t_load = time.monotonic()
        load_breakdown: dict[str, float] = {}
        try:
            # Prefer passing the timings dict so the loader can record its own
            # sub-phases (harbor: extract + checklist-gen). The load runs under a
            # wall-clock ceiling (LOAD_TIMEOUT_S) on its own thread: the extract +
            # checklist-gen phase is otherwise unbounded, and a CPU-starved trivial
            # load would hold its slot until the 1800s driver stall-timeout killed
            # the whole shard. _load_with_timeout re-raises the loader's own
            # exception here so the branches below are unchanged.
            task = _load_with_timeout(load_fn, tid, load_breakdown, LOAD_TIMEOUT_S)
        except LoadTimeout as exc:
            # Same contract as TaskInputMissing: a starved load is an environment
            # fault, so write NO verdict.json and leave the task for the next pass
            # (which, less loaded, verifies it fine) instead of freezing a 0.0.
            print(f"[harbor] LOAD TIMEOUT {tid}: {exc} — no verdict written, "
                  f"task left for retry", flush=True)
            return i, {"task_id": tid, "method": "interactive",
                       "load_timeout": True, "error": str(exc)[:200]}
        except TaskInputMissing as exc:
            # The INPUT is gone, so there is nothing to verify and nothing true
            # to record. Deliberately NO verdict.json: the resume check is
            # existence-based, so persisting here would freeze an environment
            # fault as a permanent 0.0 that never gets retried. Returning an
            # unwritten error row keeps the task in the work queue and makes the
            # count visible in the run summary instead of hiding it among the
            # genuine zeros.
            print(f"[harbor] INPUT MISSING {tid}: {exc} — no verdict written, "
                  f"task left for retry", flush=True)
            return i, {"task_id": tid, "method": "interactive",
                       "input_missing": True, "error": str(exc)[:200]}
        except Exception as exc:  # noqa: BLE001
            v = pipeline._error_verdict("load", exc, renderable=False)
            v["task_id"] = tid
            v["timings"] = {"load_s": round(time.monotonic() - t_load, 2), **load_breakdown}
            pipeline.write_json_atomic(out_dir / tid / "verdict.json", v)
            return i, v
        load_s = round(time.monotonic() - t_load, 2)
        # Persist the checklist-gen provenance (raw prompt/output) + token cost as a
        # per-task sidecar the moment we have it, so it survives even if a later
        # stage crashes. gen_meta is only present for the Harbor loader.
        gen_meta = task.pop("gen_meta", None) if isinstance(task, dict) else None
        if gen_meta:
            pipeline.write_json_atomic(out_dir / tid / "checklist_gen.json", gen_meta)
        page, finalize = _worker_page()
        # v0.70 — the trajectory browser is launched ONCE per worker thread and
        # reused across tasks, so its watchdog ceiling is set PER TASK here: raise
        # it for a detected heavy-3D artifact, and always restore it in the finally
        # so the next (possibly plain) task on this browser is caught at base.
        if isinstance(task, dict) and task.get("heavy_3d"):
            browser_env.set_tree_heavy(True)
        try:
            verdict = pipeline.verify_task(task, page, out_dir=out_dir / tid,
                                           vlm_call=vlm_call, text_call=text_call,
                                           aesthetics_call=aesthetics_call,
                                           planner_call=planner_call,
                                           plan_override=(plan_override_fn(tid)
                                                          if plan_override_fn else None),
                                           max_frames=max_frames, max_steps=max_steps,
                                           step_timeout_s=step_timeout_s)
        finally:
            # Restore the reused browser's base watchdog ceiling for the next task,
            # even if verify raised. Unconditional: a no-op when the tree was never
            # flagged, and cheap. (v0.70)
            browser_env.set_tree_heavy(False)
            # In a `finally` so a crashing task still gives its disk back: on a
            # 17,833-instance sweep the extracted trees are the binding limit, and
            # the tasks most likely to raise are the huge ones.
            _release_workspace(task)
        # The pipeline recorded plan/exec/score/aes durations; load + finalize
        # happen OUT here (around the pipeline), so we fold them in and compute a
        # single task_total the [timing] line prints.
        tm = verdict.get("timings") or {}
        tm["load_s"] = load_s
        tm.update(load_breakdown)  # load_extract_s / load_checklist_s (harbor)
        tm["finalize_s"] = 0.0  # refined after finalize; a pre-finalize value in
                                # case a wedged/raising close never gets there.
        tm["task_total_s"] = round(load_s + tm.get("verify_total_s", 0.0), 2)
        verdict["timings"] = tm
        # Surface checklist-gen cost on the verdict so the run summary can total it.
        if gen_meta:
            verdict["gen_cost"] = {"total_tokens": gen_meta.get("total_tokens", 0),
                                   "cost_usd": gen_meta.get("cost_usd", 0.0)}
        # DEFECT 117: PERSIST THE VERDICT *BEFORE* FINALIZE. verify_task has already
        # returned a complete verdict; finalize is pure teardown (close context,
        # copy video, close browser, p.stop). The rare finalize-wedge tail
        # (browser_env.py:283-296) used to lose this verdict because it was written
        # after the close — on resume the id re-entered the todo set and re-wedged.
        # Writing it here means a wedged close costs at most a leaked Chromium (the
        # reaper / OS reaps it) and one video.webm, never a verdict, and the resume
        # filter (DEFECT 105) correctly skips the id. write_json_atomic is a
        # few-KB atomic rename — negligible next to a seconds-to-minutes task.
        pipeline.write_json_atomic(out_dir / tid / "verdict.json", verdict)
        # Finalize runs INLINE on this worker thread — never a daemon thread.
        # Playwright's sync API is greenlet-thread-affine: context.close /
        # browser.close / p.stop can only be called from the thread that started
        # sync_playwright(). Off-thread they throw "Cannot switch to a different
        # thread" and leave the asyncio loop up, crashing the next task's start()
        # and taking the whole shard down rc=1 (DEFECT 117). Against a
        # watchdog-killed tree an inline close returns (the watchdog kills browser
        # AND driver, so the close gets "Connection closed while reading from the
        # driver" in ~5s; browser_env.py:283-296). The only residual wedge is the
        # UNEXPLAINED multi-instance tail — netted by the reaper + 1800s stall-kill,
        # with the verdict already safe on disk above.
        t_fin = time.monotonic()
        finalize(out_dir / tid)
        if hasattr(local, "pf"):
            del local.pf  # drop the page so the NEXT task on this thread gets a
                          # fresh context+video (video recording is per-context).
        tm["finalize_s"] = round(time.monotonic() - t_fin, 2)
        tm["task_total_s"] = round(load_s + tm.get("verify_total_s", 0.0)
                                   + tm["finalize_s"], 2)
        # Re-persist with the refined finalize timing (normal path). If finalize
        # raised, this second write is skipped and the pre-finalize verdict above
        # stands — the verdict is identical bar the finalize_s timing.
        pipeline.write_json_atomic(out_dir / tid / "verdict.json", verdict)
        _lb = (f" [extract={load_breakdown.get('load_extract_s')} "
               f"checklist={load_breakdown.get('load_checklist_s')}]" if load_breakdown else "")
        _gc = (f" gen_tok={gen_meta.get('total_tokens')} gen_$={gen_meta.get('cost_usd')}"
               if gen_meta else "")
        print(f"[timing] {tid} total={tm['task_total_s']}s "
              f"load={load_s}{_lb} plan={tm.get('plan_s')} exec={tm.get('execute_s')} "
              f"score={tm.get('score_s')} aes={tm.get('aesthetics_s')} "
              f"fin={tm['finalize_s']}{_gc}", flush=True)
        verdict["task_id"] = tid
        with lock:
            # Rewrite the run-level summary after every task (under a lock, since
            # workers finish concurrently) so a Ctrl-C mid-batch still leaves a
            # summary covering everything scored so far.
            done = [r for r in rows if r is not None]
            write_summary(out_dir, done + [verdict])
        return i, verdict

    # pool.map preserves input order, and we also index by i, so `rows` stays
    # aligned to `ids` regardless of completion order.
    n_slots = max(1, min(workers, len(ids)))
    # Tell the reaper how big the pool is (DEFECT 106). Without this it can only
    # apply its absolute threshold, which a small pool never reaches: on a 4-slot
    # tail shard, 2 wedged slots is half the work lost and 2 < 3 forever. The
    # effective size is min(workers, len(ids)) -- ThreadPoolExecutor creates
    # threads lazily, so a 12-worker pool given 4 tasks only ever has 4 slots, and
    # a fraction computed against 12 would be wrong in the same direction.
    browser_env.REAPER_POOL_SIZE = n_slots
    with ThreadPoolExecutor(max_workers=n_slots) as pool:
        for i, v in pool.map(_one, list(enumerate(ids))):
            rows[i] = v
    write_summary(out_dir, rows)
    return rows


def _build_page_factory(record_video: bool | None = None):
    """Factory yielding (page, finalize). finalize(out_dir) closes the context and,
    when video recording is on, copies the recording to out_dir/video.webm.

    Video is OFF by default because nothing downstream reads it. On the b0811
    sweep, `finalize` — which is almost entirely Playwright flushing the .webm —
    was the single largest cost in the pipeline: median 1087s per task against a
    766s median for the actual execution, 59.3 of ~110 total hours. Scoring,
    aesthetics and the selection report all work from the PNG frames; the video
    is a debugging aid only. Set IV_RECORD_VIDEO=1 (or pass record_video=True) to
    turn it back on for a single-task investigation. Video recording is
    per-context, so this decides whether a context records at creation time."""
    from playwright.sync_api import sync_playwright
    import shutil

    if record_video is None:
        record_video = os.environ.get("IV_RECORD_VIDEO", "") not in ("", "0", "false")

    def factory():
        p = sync_playwright().start()
        # Force the SwiftShader software-GL backend so 3D games get a
        # deterministic WebGL2 context (see browser_env). Plain headless
        # launch auto-picks a backend that intermittently yields no WebGL,
        # dropping whole 3D games into their "NO WEBGL" fallback.
        # launch_guarded arms the wedge/balloon watchdog as part of launching.
        browser = launch_guarded(p.chromium, headless=True, args=chromium_launch_args())
        # Viewport, stated explicitly rather than inherited. Playwright's default
        # happens to be 1280x720, which is what every b0811 trajectory actually
        # drove — but the planner prompt tells the model that number and the
        # planner/executor DISCARD coordinates outside it, so the three must
        # agree by construction, not by coincidence. On b0811 nobody had ever
        # asserted it and 183 before_after probes aimed below the fold at a page
        # the model imagined taller (y up to 1040), landing on nothing.
        ctx_kw: dict = {"viewport": {"width": planner.DRIVE_VP[0],
                                     "height": planner.DRIVE_VP[1]}}
        if record_video:
            vid_tmp = Path(rolling_paths.ROLLING_ROOT / "verify_runs" / "_vid_tmp")
            vid_tmp.mkdir(parents=True, exist_ok=True)
            ctx_kw["record_video_dir"] = str(vid_tmp)
        context = browser.new_context(**ctx_kw)
        page = context.new_page()

        def finalize(out_dir: Path):
            try:
                vid = page.video if record_video else None
                context.close()  # flush the video file (when recording)
                if vid is not None:
                    src = vid.path()
                    shutil.copyfile(src, Path(out_dir) / "video.webm")
            except Exception:
                pass
            finally:
                # Release AFTER the close, never before. `browser.close()` is
                # itself a Playwright call and can wedge like any other; if the
                # marker were already cleared, this thread would be invisible to
                # the reaper for the life of the process (DEFECT 109). Leaving it
                # set means a close that never returns is still detectable, and
                # `release_marker` runs only on the paths where the close DID
                # return -- including the raising one, hence the nested finally.
                try:
                    browser.close(); p.stop()
                except Exception:
                    pass
                finally:
                    release_marker()
        return page, finalize

    return factory


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="3-stage interactive artifact verifier")
    ap.add_argument("--run", required=True)
    ap.add_argument("--subset", default=None)
    ap.add_argument("--owner", default="gji")
    ap.add_argument("--source", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tasks", default=None, help="comma-separated task ids")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-frames", type=int, default=12)
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--step-timeout", type=int, default=15)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--vlm-model", required=True)
    ap.add_argument("--text-model", required=True)
    ap.add_argument("--aesthetics-model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    source_path = loader.resolve_source_path(args.subset, args.owner, args.source)
    source_index = loader.index_source(source_path)
    if args.tasks:
        ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    else:
        ids = loader.list_task_ids(args.run, args.subset, args.owner)
    if args.limit:
        ids = ids[:args.limit]

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = resolve_out_dir(args.out, ts)
    pipeline.write_json_atomic(out_dir / "config.json", {
        "run": args.run, "subset": args.subset, "owner": args.owner,
        "source": source_path, "count": len(ids), "workers": args.workers,
        "vlm_model": args.vlm_model, "text_model": args.text_model,
        "aesthetics_model": args.aesthetics_model,
        "base_url": args.base_url, "max_frames": args.max_frames,
        "max_steps": args.max_steps, "step_timeout_s": args.step_timeout, "ts": ts})

    vlm_call, text_call, aesthetics_call, planner_call = models.build_calls({
        "base_url": args.base_url, "api_key": args.api_key,
        "vlm_model": args.vlm_model, "text_model": args.text_model,
        "aesthetics_model": args.aesthetics_model})

    print(f"[interactive_verify] {len(ids)} task(s) -> {out_dir}", flush=True)
    rows = run_batch(ids, args.run, source_index, out_dir, vlm_call, text_call,
                     workers=args.workers, page_factory=_build_page_factory(),
                     force=args.force, max_frames=args.max_frames,
                     max_steps=args.max_steps, step_timeout_s=args.step_timeout,
                     aesthetics_call=aesthetics_call, planner_call=planner_call)
    agg = _summary_rows(rows)
    print(f"[interactive_verify] done: {agg}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
