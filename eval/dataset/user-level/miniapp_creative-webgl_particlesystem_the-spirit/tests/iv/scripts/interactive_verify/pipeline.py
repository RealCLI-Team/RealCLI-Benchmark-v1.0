#!/usr/bin/env python3
"""Glue the three stages into a single per-task verification.

This is the SPINE of the verifier. One task flows through it as:

    render initial page
      -> Stage 1  planner.plan        : VLM proposes an action plan     -> plan.json
      -> Stage 2  executor.run_...    : Playwright replays it, screenshots-> trajectory.json
      -> Stage 3  scorer.score        : judge each checklist item        -> verdict.json
      -> Stage 3b aesthetics.score_...: independent visual-quality grade  -> verdict["aesthetics"]

Each stage's wall-clock is recorded in ``timings`` as it goes. Every stage is
wrapped so a failure becomes an *error verdict* rather than an exception: a
batch of hundreds of artifacts must never die on one bad page (same convention
as service.verify.worker.drain_once). All outputs are written atomically so a
crash mid-write never leaves a half-verdict on disk.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from scripts.interactive_verify import (aesthetics, element_inventory, executor,
                                        planner, scorer)

_MARK = {True: "✅", False: "❌", None: "❔"}


def write_json_atomic(path: Path, obj: Any) -> None:
    """Write JSON via a temp file + atomic rename, so a reader (or a crash) never
    sees a partially-written verdict — the file either has the old content or the
    complete new content, never a truncated middle.

    DEFECT 75: the temp path must be unique PER WRITER, not just per target.
    A fixed `<name>.tmp` is only atomic for one writer. Concurrent drivers
    sharing an out dir all write the same target — `config.json` at the run
    root — so two of them raced: A created `config.json.tmp`, B overwrote it,
    A renamed it away, and B's `os.replace` died `FileNotFoundError`. Observed
    killing a whole acceptance shard (`accept_d74b/s02`, rc=1) AFTER its tasks
    had been verified, so the work was done and the run still reported failure.
    The full sweep is 8 lanes sharing one out dir, i.e. the same race with eight
    participants, which would have surfaced as lanes dying at random.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        # Never leave a stray temp behind: a crashed writer's leftovers would
        # accumulate in an out dir that other lanes are still scanning.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def render_report(task: dict[str, Any], verdict: dict[str, Any]) -> str:
    lines = [f"# Interactive verification — {task.get('task_id')}"]
    if verdict.get("error"):
        lines.append(f"\n> ⚠️ error: {verdict['error']}")
    lines.append(f"\n**Score:** {verdict.get('score', 0.0):.3f} "
                 f"(renderable={verdict.get('renderable')}, "
                 f"{verdict.get('satisfied', 0)}/{verdict.get('determinate', 0)} satisfied, "
                 f"{verdict.get('indeterminate', 0)} indeterminate)")
    # Build defects are advisory (score is untouched) but load-bearing for
    # triage, so they get a prominent callout when present.
    for d in verdict.get("build_defects", []) or []:
        if d.get("retracted"):
            # Kept visible, not silently dropped: the observation was real and a
            # reader auditing the detector needs to see both it and why it was
            # withdrawn (DEFECT 92).
            lines.append(f"\n> ↩️ **build defect RETRACTED** [{d.get('kind')}]: "
                         f"{d.get('detail')}\n>\n> _{d.get('retraction_reason')}_")
        else:
            lines.append(f"\n> 🔧 **build defect** [{d.get('kind')}]: {d.get('detail')}")
    for it in verdict.get("items", []):
        r = it.get("result", {})
        label = it.get("title") or it.get("checking_content")
        wt = it.get("weight", 1.0)
        wtag = f" (w={wt})" if wt != 1.0 else ""
        lines.append(f"- {_MARK.get(r.get('satisfied'))} {label}{wtag} "
                     f"— {r.get('rationale', '')}")
    aes = verdict.get("aesthetics")
    if aes:
        lines.append(f"\n**Aesthetics:** {aes.get('total_100')}/100 "
                     f"(render_ok={aes.get('render_ok')})")
        for d in ("layout", "color", "typography", "craft", "interaction"):
            sc = aes.get("scores", {}).get(d)
            lines.append(f"- {d}: {sc}/4 — {aes.get('reasons', {}).get(d, '')}")
        if aes.get("flags"):
            lines.append(f"- flags: {', '.join(aes['flags'])}")
    return "\n".join(lines) + "\n"


def _finalize_timings(timings: dict[str, float]) -> dict[str, float]:
    """Attach a per-task total (sum of the recorded stage durations)."""
    timings["verify_total_s"] = round(sum(timings.values()), 2)
    return timings


def _error_verdict(stage: str, exc: Exception, renderable: bool) -> dict[str, Any]:
    return {"score": 0.0, "renderable": renderable, "method": "interactive",
            "checklist_source": "embedded", "total_items": 0, "determinate": 0,
            "indeterminate": 0, "satisfied": 0, "by_tier": {}, "by_mode": {},
            "gate_reason": None, "items": [], "frames": [],
            "aesthetics": None, "build_defect": False, "build_defects": [],
            "error": f"{stage}: {str(exc)[:200]}"}


# Wall-clock bound on the whole score stage (scorer.score's judge pool.map).
# DEFECT 116. Each checklist item is one blocking judge HTTP call routed through
# LLMClient, whose only bound is a per-recv socket timeout (300s) × max_retries
# (6). A gateway that trickles bytes — sends *some* data every <300s without ever
# completing the body — never trips that recv timeout, so a single urlopen().read()
# can block indefinitely; `pool.map`'s ordered iterator then waits on that one
# future forever, hanging the task INSIDE verify_task, upstream of finalize (so
# _finalize_with_timeout cannot see it) and upstream of the verdict write (so no
# verdict is persisted). Measured on this run: the 30-min driver stall-kill is the
# only net that caught it, and it caught it twice for the same instance — killing
# the WHOLE shard each time. Real score latency here is p50 55s / p90 252s /
# p99 416s / max 810s over 206 tasks, so 1200s (~1.5× the observed max, well under
# the 1800s stall window) fires only on a genuine hang, never on a slow-but-live
# score. See _score_with_timeout.
SCORE_TIMEOUT_S = float(os.environ.get("IV_SCORE_TIMEOUT_S", "1200"))


class ScoreTimeout(Exception):
    """scorer.score exceeded SCORE_TIMEOUT_S — a wedged judge call, not a bad page."""


def _score_with_timeout(score_fn: Callable[[], dict[str, Any]],
                        timeout_s: float) -> dict[str, Any]:
    """Run ``score_fn()`` on a daemon thread; raise ScoreTimeout after ``timeout_s``.

    DEFECT 116. Same daemon-thread-with-deadline shape as run._load_with_timeout
    and run._finalize_with_timeout, but placed here so it wraps the ENTIRE score
    stage (the judge ``pool.map``), which is where the unbounded wedge lives. On
    timeout we raise ScoreTimeout, which flows into verify_task's existing
    ``except`` around the score call — exactly the path a raised scorer error
    already takes: an ``_error_verdict("score", ...)`` is built, aesthetics still
    runs on the frames Stage 2 captured, and the verdict IS persisted. So a
    wedged score degrades to a scored=0 error verdict for that one task instead
    of consuming the shard's whole 1800s stall budget and killing every sibling
    with it. The abandoned daemon thread keeps spinning on the dead socket but
    cannot block interpreter exit or hold the pool future (the pool lives inside
    it, not around it). Losing this one task's functional grade is the correct
    trade: the alternative is losing the grade AND the whole shard.
    """
    box: dict[str, Any] = {}

    def _work():
        try:
            box["verdict"] = score_fn()
        except BaseException as exc:  # noqa: BLE001 — carry it out to the caller's thread
            box["exc"] = exc

    th = threading.Thread(target=_work, name="score", daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        raise ScoreTimeout(f"score exceeded {timeout_s:.0f}s (wedged judge call)")
    if "exc" in box:
        raise box["exc"]
    return box["verdict"]


def _retract_refuted_defects(defects: list[dict[str, Any]],
                             verdict: dict[str, Any]) -> list[dict[str, Any]]:
    """Withdraw a boot-time defect the run's own later evidence refutes.

    DEFECT 92. `entry-gate-stuck` is emitted during boot, BEFORE the plan runs,
    and its wording is a PREDICTION: "the artifact's own boot/gate teardown never
    ran, so every interaction below it is unreachable." The run then goes and
    produces exactly the evidence that prediction is about, so the flag can be
    checked against its own consequence — no new probe, no extra cost.

    Why it needs checking: the probe walks up from the viewport centre to the
    outermost POSITIONED element covering >=55% of the viewport. A full-screen
    app legitimately mounts one — `#stage`, `#app`, `#scene`. Finding no
    pressable child inside it, `_GATE_PROBE_JS` falls back to `best || cover`
    and clicks the application root as though it were a dismissable sheet. The
    root of course survives (it IS the app), so the gate is reported stuck.
    `_GATE_MAX_CONTROLS` guards the opposite error (too many controls => it is
    the artifact's own menu, defect 83); nothing guarded zero controls.

    Measured on b0811 full_v028 (12,158 rendered artifacts): the flag fired on
    6,631 (54.5%), and 1,601 of the 5,083 that went on to be scored reached
    >=0.80 with 1,298 at EXACTLY 1.0 — impossible if nothing below the gate was
    reachable. Score separation between flagged and unflagged is weak in both
    directions (mean 0.340 vs 0.435, p90 = 1.0 in BOTH arms), which is the
    signature of a detector describing the corpus rather than a defect.

    The discriminator cannot be lexical. Measured (`_gate_discriminator.py`):
    `#boot` appears 165x among demonstrably-false flags and 708x among plausibly
    true ones; `#stage` 558x vs 562x; a gate-ish/app-ish name regex splits
    25.9%/48.2% and 55.4%/38.1%. Nor can it be the presence of a control: the
    detail string renders the label with `!r`, so '<none>' arrives as literal
    text and any regex over it matches 100% of both groups.

    What separates them is whether any checklist item came back SATISFIED. A
    satisfied item means the judge saw the requirement met in the frames, which
    cannot happen behind a curtain that never lifted. Measured against score as
    ground truth: it retracts 100% of the 1,601 false flags. It also fires on
    53.8% of score==0 flagged rows, which looks like over-firing until that
    bucket is split (`_gate_retraction_safety.py`) — the rows there with a
    satisfied item carry ungated_score mean 0.515 (28.7% of items satisfied) and
    are live artifacts zeroed by the critical-item hard gate, while the rows
    without one carry ungated_score mean EXACTLY 0.000, no satisfied items, and
    81.2% indeterminate. So the rule fires on no row that shows any sign of
    being dead; the ground-truth label was wrong, not the rule.

    Retract rather than delete. The probe's observation stands (something did
    cover the viewport and did not respond to clicks) and the frames are the
    only record of what we clicked; a consumer auditing the detector needs both.
    `build_defect` stops counting it, so selection no longer discards the
    artifact, and `retracted`/`retraction_reason` say why.
    """
    if not defects:
        return []
    if not any(isinstance(d, dict) and d.get("kind") == "entry-gate-stuck"
               for d in defects):
        return list(defects)
    n_sat = sum(1 for it in (verdict.get("items") or [])
                if (it.get("result") or {}).get("satisfied") is True)
    if not n_sat:
        return list(defects)
    out: list[dict[str, Any]] = []
    for d in defects:
        if isinstance(d, dict) and d.get("kind") == "entry-gate-stuck":
            d = dict(d)
            d["retracted"] = True
            d["retraction_reason"] = (
                f"refuted by this run's own evidence: {n_sat} checklist item(s) were "
                "judged SATISFIED from the captured frames, which is impossible if "
                "the gate never lifted. The covering element was most likely the "
                "artifact's own application root (no pressable child inside it), "
                "not a boot overlay.")
        out.append(d)
    return out


def verify_task(task: dict[str, Any], page: Any, *, out_dir: Path,
                vlm_call: Callable, text_call: Callable,
                aesthetics_call: Callable | None = None,
                planner_call: Callable | None = None,
                plan_override: dict[str, Any] | None = None,
                max_frames: int = 12, max_steps: int = 40,
                step_timeout_s: int = 15) -> dict[str, Any]:
    """Verify one artifact. Wrapper exists only to guarantee the temp sweep.

    DEFECT 51: every `executor.observe()` mkstemps a PNG which `_persist` COPIES
    into `frames/`, and nothing unlinked the original. A 60-step task leaks ~60
    files; the b0811 sweep left 128,438 stray `/tmp/tmp*.png` totalling 65 GB and
    drove the filesystem to 83% full. The sweep belongs here rather than at the
    end of `run_trajectory` because the PLANNER's initial observation is taken
    before the trajectory starts and would otherwise still leak, and because
    `verify_task` has three return paths plus exceptions -- a `finally` is the
    only shape that covers all of them.
    """
    try:
        return _verify_task(
            task, page, out_dir=out_dir, vlm_call=vlm_call, text_call=text_call,
            aesthetics_call=aesthetics_call, planner_call=planner_call,
            plan_override=plan_override, max_frames=max_frames,
            max_steps=max_steps, step_timeout_s=step_timeout_s)
    finally:
        executor._sweep_shot_tmps(page)


def _ground_retries() -> int:
    """Regeneration attempts for the defect-103 grounding check (env-overridable).

    Read per task rather than cached: a long batch can be re-armed without a
    restart, and the A/B arms differ by this one value and nothing else.
    """
    try:
        return max(0, int(os.environ.get("IV_GROUND_RETRIES", "1")))
    except (TypeError, ValueError):
        return 1


def _env_int(name: str, default: int, lo: int = 0) -> int:
    try:
        return max(lo, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _inventory_pages() -> int:
    """Linked pages walked for the control inventory (0 = entry page only).

    Deliberately independent of whether the planner is SHOWN the result. The
    walk is the only part that costs wall clock, and determinacy is sensitive to
    wall clock at a constrained CPU quota, so both A/B arms must pay the same
    cost or a determinacy delta is not attributable to the prompt change.
    """
    return _env_int("IV_INVENTORY_PAGES", element_inventory.MAX_PAGES)


def _show_inventory() -> bool:
    """Whether the planner SEES the inventory. IV_PLAN_INVENTORY=0 is the control
    arm: the inventory is still built and still recorded on plan.json, so a paired
    comparison knows what the planner would have been told."""
    return os.environ.get("IV_PLAN_INVENTORY", "1").strip() not in ("0", "false", "no")


def _verify_task(task: dict[str, Any], page: Any, *, out_dir: Path,
                 vlm_call: Callable, text_call: Callable,
                 aesthetics_call: Callable | None = None,
                 planner_call: Callable | None = None,
                 plan_override: dict[str, Any] | None = None,
                 max_frames: int = 12, max_steps: int = 40,
                 step_timeout_s: int = 15) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    entry_url = task["entry_url"]
    timings: dict[str, float] = {}

    # Stage 1 — render the landing page, then ask the VLM for ONE action plan.
    # We navigate + settle + observe here (not inside the planner) so the same
    # initial screenshot/DOM the planner reasons over is captured once.
    t0 = time.monotonic()
    try:
        executor.navigate(page, entry_url)
        executor.settle(page)              # let SPAs mount/hydrate before we look
        init_obs = executor.observe(page)  # {dom_snapshot, screenshot} the planner sees
        if plan_override:
            # Gate-round A/B only (--reuse-plan-from): drive a PRIOR run's exact
            # action sequence so a score delta is attributable to the executor
            # rather than to a re-sampled plan. The planner is not called at all,
            # and we still navigate/settle/observe above because the executor's
            # first frame and the report's initial screenshot come from there.
            plan_obj = {**plan_override, "plan_source": "reused"}
        else:
            # DEFECT 97: the artifact's own pages, read from the LIVE DOM, so
            # `goto` targets are addressed rather than guessed out of a DOM
            # snapshot the planner only sees the first 6 KB of.
            init_obs = {**init_obs,
                        "links": executor.same_origin_links(page, entry_url)}
            # Tier-2 observation: role + accessible name for every control on
            # every page. The planner previously saw one screenshot and 6 KB of
            # the ENTRY page's innerText, while checklist generation was already
            # being handed a pruned AX tree (`harbor_loader.capture_axtree`) — so
            # the checklist author knew a control was a slider and the planner
            # writing that item's drag did not. Runs in its own browser context
            # and reuses the AX tree observe() already took, so it can neither
            # latch `_ax_too_slow` on the scored page nor share its localStorage.
            inventory = element_inventory.build(
                page, entry_url, init_obs["links"], init_obs.get("ax"),
                max_pages=_inventory_pages())
            if _show_inventory():
                init_obs = {**init_obs, "inventory": inventory}
            # DEFECT 103: the workspace is the planner's ground truth. It holds
            # every page and component of the artifact, so a target string absent
            # from it is absent from the artifact — which is what lets the planner
            # be told it planned against a product it remembers rather than the
            # thing that was built. Absent/unreadable => the check abstains.
            # IV_GROUND_RETRIES=0 keeps the grounding *measurement* but performs
            # no regeneration. That is the A/B control arm: both arms record what
            # the check found, only one acts on it, so a paired comparison does
            # not need two payloads that could differ in some other way.
            plan_obj = planner.plan(task["prompt"], init_obs, task["items"],
                                    planner_call or vlm_call,
                                    workspace=task.get("workspace"),
                                    ground_retries=_ground_retries())
        # Census the emitted plan for the three b0811 defect signatures (capture
        # on a `wait`, out-of-viewport coordinate, semantic duplicate probe).
        # These produce confident WRONG verdicts rather than errors, so they are
        # invisible in the scores — recording them on the plan turns a regression
        # into a countable number in the run report. Advisory only: the plan still
        # runs, because a defective probe is better evidence than no probe.
        # Recorded whether or not the planner was shown it: the control arm needs
        # to know what it withheld for the comparison to mean anything.
        if not plan_override:
            plan_obj["inventory"] = inventory
            plan_obj["inventory_shown"] = _show_inventory()
        try:
            from scripts.interactive_verify._smoke_planguard import audit
            defects = audit(plan_obj.get("actions") or [])
            if defects:
                plan_obj["plan_defects"] = defects
        except Exception:  # noqa: BLE001 — a census must never fail a task
            pass
        write_json_atomic(out / "plan.json", plan_obj)
    except Exception as exc:  # noqa: BLE001 (incl. planner.PlanError)
        timings["plan_s"] = round(time.monotonic() - t0, 2)
        verdict = _error_verdict("plan", exc, renderable=bool((init_obs or {}).get("dom_snapshot"))
                                 if "init_obs" in dir() else False)
        verdict["timings"] = _finalize_timings(timings)
        write_json_atomic(out / "verdict.json", verdict)
        (out / "report.md").write_text(render_report(task, verdict), encoding="utf-8")
        return verdict
    timings["plan_s"] = round(time.monotonic() - t0, 2)

    # Stage 2 — replay the plan in the real browser, capturing a frame before the
    # first action and after every action (the visual evidence Stage 3 scores on).
    t0 = time.monotonic()
    try:
        trajectory = executor.run_trajectory(page, plan_obj["actions"], entry_url,
                                              out_dir=out, max_steps=max_steps,
                                              step_timeout_s=step_timeout_s,
                                              needs_mobile=bool(task.get("needs_mobile")))
        write_json_atomic(out / "trajectory.json", trajectory)
    except Exception as exc:  # noqa: BLE001
        timings["execute_s"] = round(time.monotonic() - t0, 2)
        verdict = _error_verdict("execute", exc, renderable=False)
        verdict["timings"] = _finalize_timings(timings)
        write_json_atomic(out / "verdict.json", verdict)
        (out / "report.md").write_text(render_report(task, verdict), encoding="utf-8")
        return verdict
    timings["execute_s"] = round(time.monotonic() - t0, 2)

    # Stage 3 — the functional verdict: judge each checklist item against the
    # trace + code + frames. Unlike Stages 1/2, a scorer failure does NOT return
    # early; we fall through to an error verdict but STILL run aesthetics below,
    # since visual grading only needs the screenshots Stage 2 already captured.
    t0 = time.monotonic()
    try:
        # DEFECT 116: bound the whole score stage on wall-clock. scorer.score's
        # judge pool.map has no wall-clock bound of its own (only a per-recv
        # socket timeout that a trickle-stalling gateway never trips), so a wedged
        # judge call hangs here forever — upstream of both finalize and the verdict
        # write. On timeout ScoreTimeout is raised and handled exactly like a
        # raised scorer error below: error verdict, aesthetics still runs, verdict
        # still persisted. See _score_with_timeout.
        verdict = _score_with_timeout(
            lambda: scorer.score(task["items"], trajectory, task["code"],
                                 vlm_call, text_call, frames_dir=out / "frames",
                                 max_frames=max_frames),
            SCORE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 (incl. ScoreTimeout)
        verdict = _error_verdict("score", exc, renderable=bool(trajectory.get("renderable")))
    timings["score_s"] = round(time.monotonic() - t0, 2)

    # Build defects (annotate-only): a page can render + partially pass yet be
    # functionally dead — a forced-visible fallback overlay covering the game, or
    # an unreachable CDN dependency. The executor detects these at load time; we
    # copy them onto the verdict WITHOUT touching the score, so downstream
    # training-signal consumers can distinguish "genuinely broken build" from
    # "merely low-scoring" without re-deriving it. (Per design: annotate, don't
    # gate — the functional score stands on its own.)
    defects = _retract_refuted_defects(trajectory.get("build_defects") or [], verdict)
    verdict["build_defects"] = defects
    verdict["build_defect"] = any(not d.get("retracted") for d in defects
                                  if isinstance(d, dict))
    # Coverage, not quality: the exec budget stopped the action loop early, so
    # some planned interactions were never attempted. Their items have no
    # before/after pair and degrade to indeterminate, but a consumer selecting
    # training data should still be able to see that this task's score rests on
    # partial coverage rather than treat it as a clean verdict.
    verdict["exec_truncated"] = bool(trajectory.get("exec_truncated"))
    # Plan-defect census (advisory, from the Stage-1 audit above). Carried onto
    # the verdict so a batch summary can total it: these defects yield confident
    # wrong verdicts, not errors, so the count is the only way a regression in
    # the planner or the generator prompt shows up without a hand audit.
    verdict["plan_defects"] = list(plan_obj.get("plan_defects") or [])

    # Stage 3b — aesthetics: an INDEPENDENT visual-quality score (does the page
    # look good?), orthogonal to whether features work. Defaults to the VLM call
    # when no dedicated aesthetics model was wired. score_aesthetics never raises,
    # but we double-guard here so a bug can't sink the whole task at the finish.
    t0 = time.monotonic()
    aes_call = aesthetics_call or vlm_call
    try:
        verdict["aesthetics"] = aesthetics.score_aesthetics(
            trajectory.get("evidence") or {"desktop": None, "mobile": None, "interaction": []},
            task["code"], aes_call, frames_dir=out / "frames",
            needs_mobile=bool(task.get("needs_mobile")))
    except Exception:  # noqa: BLE001 — defensive; score_aesthetics already guards
        verdict["aesthetics"] = None
    timings["aesthetics_s"] = round(time.monotonic() - t0, 2)

    verdict["timings"] = _finalize_timings(timings)
    write_json_atomic(out / "verdict.json", verdict)
    (out / "report.md").write_text(render_report(task, verdict), encoding="utf-8")
    return verdict
