#!/usr/bin/env python3
"""Per-artifact verification orchestration.

Given a job descriptor, produce a verdict:

  1. ``exec_fn(workspace)`` runs the (vendored) htmleval browser phases and
     returns an evidence bundle ``{renderable, code, dom, frames, trace}``.
  2. Assemble the checklist: load general (+ task-specific if authored),
     otherwise generate task-specific / artifact-specific with the generator.
  3. Judge every item against the evidence.
  4. Aggregate into a verdict (binary, renderability-gated) and write
     ``verdict.json`` + ``report.md`` + ``artifact_checklist.jsonl``.

Every external dependency (htmleval via ``exec_fn``, the judge/generator models)
is injected, so this unit-tests end-to-end without a browser or a network.
"""

from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from service.verify import agent_judge, checklist, generator, judge, scoring

# Judge items concurrently — each item is one (slow) LLM call, so a pool turns a
# 20-item checklist from ~5 min of serial calls into a couple of batches. These
# are LIGHT threads (network I/O), not the memory-heavy Chromium (one browser per
# artifact, artifacts processed one at a time). Overridable via env so a pod can
# cap it; kept modest to bound the judge endpoint's concurrent load.
JUDGE_WORKERS = int(os.environ.get("QA_VERIFY_JUDGE_WORKERS", "8"))


# Fallback general checks used when no checklist could be assembled (e.g. the
# generator model errored). Keeps every renderable artifact scoreable rather than
# losing it entirely — generation failure degrades coverage, not the whole verdict.
_DEFAULT_GENERAL = [
    {"task_id": "_", "check_item_id": "1",
     "checking_content": "The page loads and shows visible, non-blank content",
     "mode": "VLM Judge", "item_type": "general", "expected_result": "a non-blank rendered UI"},
    {"task_id": "_", "check_item_id": "2",
     "checking_content": "There are no fatal JavaScript errors that break the page",
     "mode": "LLM Judge", "item_type": "general",
     "expected_result": "no uncaught errors that halt rendering"},
    {"task_id": "_", "check_item_id": "3",
     "checking_content": "The page's layout is coherent and its text is legible",
     "mode": "VLM Judge", "item_type": "general", "expected_result": "readable, well-formed layout"},
]


def _safe_gen(fn, *args, warnings: list, label: str, failures: list | None = None,
              **kwargs) -> list[dict[str, Any]]:
    """Run a generator call; on ANY failure record a warning and return [] so one
    tier's failure never loses the whole artifact.

    ``failures`` collects the labels that raised. That list is what lets the
    caller tell an OUTAGE apart from a generator that ran fine and authored
    nothing — a distinction the warning text alone cannot carry, because nothing
    downstream reads warnings. See ``_assemble_checklist``."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{label} generation failed: {str(exc)[:160]}")
        if failures is not None:
            failures.append(label)
        return []


def _assemble_checklist(job: dict[str, Any], evidence: dict[str, Any],
                        gen_model_call: Callable, warnings: list) -> list[dict[str, Any]]:
    tid = job.get("record_key", "t")
    allowed_modes = job.get("allowed_modes")
    groups: list[list[dict[str, Any]]] = []
    gen_failed: list[str] = []
    if job.get("general_checklist_ref"):
        groups.append(checklist.load_jsonl(job["general_checklist_ref"]))
    if job.get("task_checklist_ref"):
        groups.append(checklist.load_jsonl(job["task_checklist_ref"]))
    elif job.get("gen_task_specific"):
        groups.append(_safe_gen(generator.gen_task_specific, tid, job.get("task_prompt", ""),
                                 gen_model_call, warnings=warnings, label="task-specific",
                                 failures=gen_failed, allowed_modes=allowed_modes))
    if job.get("gen_artifact_specific"):
        groups.append(_safe_gen(generator.gen_artifact_specific, tid, job.get("task_prompt", ""),
                                 evidence.get("code", ""), evidence.get("dom", ""),
                                 gen_model_call, warnings=warnings, label="artifact-specific",
                                 failures=gen_failed, allowed_modes=allowed_modes))
    merged = checklist.merge(*groups) if groups else []
    if not merged:
        if gen_failed:
            # INFRASTRUCTURE failure (gateway 503, timeout, auth), not a property
            # of this artifact. Falling through to the generic checks here is the
            # worst available behaviour: those three checks ("the page is
            # non-blank", "the layout is legible") pass on almost any artifact, so
            # an outage silently converts a whole run into confident ~1.0 verdicts
            # that carry no error and are never retried. That happened — a 50-task
            # acceptance run read 0.367 -> 0.935 with every checklist replaced.
            # Raise instead: the worker records an artifact FAILURE with this
            # cause, which is visible and retryable.
            raise RuntimeError(
                f"{tid}: checklist generation failed ({', '.join(gen_failed)}) and no "
                f"authored checklist was available; refusing to substitute the generic "
                f"checks — see warnings for the cause")
        # The generators RAN and authored nothing usable (or none were enabled):
        # a content property, not an outage. Degrade to the general checks, and
        # stamp each one so these verdicts stay countable downstream.
        warnings.append("no checklist assembled; using default general checks")
        merged = checklist.merge([dict(it) for it in _DEFAULT_GENERAL])
        for it in merged:
            it["checklist_fallback"] = True
    return merged


_MARK = {True: "✅", False: "❌", None: "❔"}


def _report_md(job: dict[str, Any], verdict: dict[str, Any],
               judged: list[dict[str, Any]]) -> str:
    lines = [f"# Verification report — {job.get('record_key')}",
             (f"\n**Score:** {verdict['score']:.3f}  "
              f"(renderable={verdict['renderable']}, "
              f"{verdict['satisfied']}/{verdict['determinate']} satisfied, "
              f"{verdict['indeterminate']} indeterminate)")]
    if verdict.get("gate_reason"):
        lines.append(f"\n> ⛔ {verdict['gate_reason']}")
    ev = verdict.get("evidence") or {}
    if ev.get("exec_error"):
        lines.append(f"\n> ⚠️ execution error: {ev['exec_error']}")
    if ev.get("page_errors"):
        lines.append(f"\n**Page errors:** {'; '.join(str(e) for e in ev['page_errors'][:5])}")
    if ev.get("console_errors"):
        errs = [e.get('text', e) if isinstance(e, dict) else e for e in ev['console_errors'][:5]]
        lines.append(f"\n**Console errors:** {'; '.join(str(e) for e in errs)}")
    if verdict.get("warnings"):
        lines.append(f"\n**Warnings:** {'; '.join(verdict['warnings'])}")
    for tier, b in verdict["by_tier"].items():
        lines.append(f"\n## {tier} — {b['score']:.2f}")
        for it in judged:
            if it["item_type"] != tier:
                continue
            r = it["result"]
            lines.append(f"- {_MARK[r['satisfied']]} {it['checking_content']} — {r['rationale']}")
    return "\n".join(lines) + "\n"


def _stage_frames(evidence: dict[str, Any], out: Path) -> None:
    """Copy the captured screenshots into ``out/frames/`` so the API frames
    endpoint (and the UI) can serve them, and rewrite ``evidence['frames']`` to
    point at the staged copies (basename-addressable)."""
    src_frames = evidence.get("frames") or []
    if not src_frames:
        return
    fdir = out / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for src in src_frames:
        try:
            dest = fdir / Path(src).name
            shutil.copyfile(src, dest)
            staged.append(str(dest))
        except OSError:
            continue
    evidence["frames"] = staged


def verify(job: dict[str, Any], exec_fn: Callable[[str], dict],
           judge_model_call: Callable, gen_model_call: Callable, *,
           out_dir, page_factory: Callable | None = None,
           judge_model_call_lang: Callable | None = None) -> dict[str, Any]:
    """Verify one artifact against its checklist.

    ``judge_model_call`` judges the two multimodal modes (VLM Judge, VLM-Based
    Browser Agent Judge) — it must reach a vision-capable model. When
    ``judge_model_call_lang`` is given, the two text-only modes (LLM Judge,
    LLM-Based Browser Agent Judge) route to it instead; otherwise they fall back
    to ``judge_model_call`` (so a single-model config behaves exactly as before).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    evidence = exec_fn(job["workspace_path"])
    _stage_frames(evidence, out)                       # copy screenshots to served dir
    warnings: list[str] = []
    items = _assemble_checklist(job, evidence, gen_model_call, warnings)

    # Apply mode filtering if job specifies allowed_modes
    allowed_modes = job.get("allowed_modes")
    if allowed_modes:
        items = checklist.filter_modes(items, allowed_modes)

    checklist.save_jsonl(out / "artifact_checklist.jsonl", items)

    # Split items by mode: agentic vs one-shot
    agentic_items: list[dict[str, Any]] = []
    one_shot_items: list[dict[str, Any]] = []

    for it in items:
        mode = it.get("mode", "")
        if "Browser Agent" in mode:
            agentic_items.append(it)
        else:
            one_shot_items.append(it)

    # Route each item to the multimodal judge (VLM modes) or the language judge
    # (LLM modes). ``_lang`` falls back to the multimodal call when no separate
    # language judge was configured — so a single-model run is unchanged.
    _lang = judge_model_call_lang or judge_model_call

    def _call_for(mode: str) -> Callable:
        return judge_model_call if mode.startswith("VLM") else _lang

    # Judge one-shot items concurrently (each is one slow LLM call). Order preserved.
    def _judge(it: dict[str, Any]) -> dict[str, Any]:
        it = dict(it)
        it["result"] = judge.judge_item(it, evidence, _call_for(it.get("mode", "")))
        return it

    one_shot_judged: list[dict[str, Any]] = []
    if one_shot_items:
        with ThreadPoolExecutor(max_workers=min(JUDGE_WORKERS, len(one_shot_items))) as pool:
            one_shot_judged = list(pool.map(_judge, one_shot_items))

    # Judge agentic items via agent_judge
    agentic_judged: list[dict[str, Any]] = []
    if agentic_items:
        if page_factory is not None:
            # Run through the agent judge
            entry_url = evidence.get("entry_url")
            if not entry_url:
                # Fallback: construct file URL from workspace
                workspace_path = job.get("workspace_path", "")
                entry_url = f"file://{workspace_path}"
            page = page_factory()
            results = agent_judge.run_session(agentic_items, entry_url, page,
                                             judge_model_call, out_dir=out,
                                             judge_model_call_lang=_lang)
            # Wrap results into full item dicts
            for item, result in zip(agentic_items, results):
                item_with_result = dict(item)
                item_with_result["result"] = result
                agentic_judged.append(item_with_result)
        else:
            # No browser available: mark all agentic items indeterminate
            for item in agentic_items:
                item_with_result = dict(item)
                item_with_result["result"] = {
                    "satisfied": None,
                    "rationale": "no browser available",
                    "evidence_ref": "agent",
                    "frames_used": []
                }
                agentic_judged.append(item_with_result)

    # Merge one-shot and agentic results, preserving original order
    judged: list[dict[str, Any]] = []
    agentic_idx = 0
    one_shot_idx = 0
    for item in items:
        mode = item.get("mode", "")
        if "Browser Agent" in mode:
            if agentic_idx < len(agentic_judged):
                judged.append(agentic_judged[agentic_idx])
                agentic_idx += 1
        else:
            if one_shot_idx < len(one_shot_judged):
                judged.append(one_shot_judged[one_shot_idx])
                one_shot_idx += 1

    verdict = scoring.aggregate(judged, renderable=bool(evidence.get("renderable")))
    verdict["items"] = judged
    verdict["method"] = "checklist"
    # Surface whether the checklist was the benchmark's own (loaded) or generated,
    # so the UI can badge the report source. Defaults to "generated".
    verdict["checklist_source"] = job.get("checklist_source") or "generated"
    verdict["warnings"] = warnings
    verdict["evidence"] = {
        "frames": [Path(f).name for f in (evidence.get("frames") or [])],
        "console_errors": (evidence.get("console_errors") or [])[:20],
        "page_errors": (evidence.get("page_errors") or [])[:20],
        "exec_error": evidence.get("error"),
    }
    (out / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (out / "report.md").write_text(_report_md(job, verdict, judged), encoding="utf-8")
    return verdict


def verify_htmleval(job: dict[str, Any], *, out_dir) -> dict[str, Any]:
    """Verify an artifact using htmleval's 5-dimension VisionEval scorer.

    Calls htmleval_exec.run_vision_eval with the job's judge_cfg (evaluator config),
    stages any frames into out_dir/frames/, writes verdict.json (the returned dict)
    and report.md (from the dict's report field), and returns the dict.

    This method is a verdict variant alongside the ALIVE checklist method.
    """
    from service.verify import htmleval_exec

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Call run_vision_eval with workspace_path and evaluator_cfg
    evaluator_cfg = job.get("judge_cfg") or {}
    verdict = htmleval_exec.run_vision_eval(
        job["workspace_path"],
        evaluator_cfg,
    )

    # Stage frames if any
    if verdict.get("frames"):
        frames_dir = out / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        staged_frames: list[str] = []
        for src in verdict.get("frames", []):
            try:
                dest = frames_dir / Path(src).name
                shutil.copyfile(src, dest)
                staged_frames.append(str(dest))
            except OSError:
                continue
        verdict["frames"] = staged_frames

    # Write verdict.json
    (out / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Write report.md
    report_text = verdict.get("report", "")
    if not report_text:
        # Generate a minimal report if none was provided
        report_text = _generate_htmleval_report(job, verdict)
    (out / "report.md").write_text(report_text, encoding="utf-8")

    return verdict


def _generate_htmleval_report(job: dict[str, Any], verdict: dict[str, Any]) -> str:
    """Generate a markdown report for the htmleval verdict if none was provided."""
    lines = [f"# htmleval VisionEval Report — {job.get('record_key', 'artifact')}"]

    if verdict.get("error"):
        lines.append(f"\n**Error:** {verdict['error']}")
        return "\n".join(lines) + "\n"

    score = verdict.get("score", 0.0)
    total = verdict.get("total_score", 0)
    lines.append(f"\n**Overall Score:** {score:.2%} ({total}/100)")
    lines.append(f"**Renderable:** {'Yes' if verdict.get('renderable') else 'No'}")

    dims = verdict.get("dims", {})
    if dims:
        lines.append("\n## Dimension Scores")
        for dim_name in ["rendering", "visual_design", "functionality", "interaction", "code_quality"]:
            dim_score = dims.get(dim_name, 0)
            lines.append(f"- **{dim_name.replace('_', ' ').title()}:** {dim_score}/100")

    return "\n".join(lines) + "\n"
