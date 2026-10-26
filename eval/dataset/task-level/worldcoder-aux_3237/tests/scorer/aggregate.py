"""Verdict shaping, reward.txt, and batch _summary.json for the official pipeline."""
from __future__ import annotations

from pathlib import Path

METHOD = "worldcoder-official"
_SKIP_MODES = {"NO_ARTIFACT", "NO_RUBRIC"}


def shape_verdict(task_id: str, raw: dict, rubric_source: str | None, status: str = "OK") -> dict:
    """Normalize a run_eval result into the pipeline verdict schema. Main score = tCov/100."""
    mode = raw.get("mode", "RUNTIME_CRASH")
    determinate = mode in ("CHECK_PASS", "CHECK_FAIL")
    tcov = float(raw.get("tCov", 0) or 0)
    score = round(tcov / 100.0, 4) if determinate else (None if mode in _SKIP_MODES else 0.0)
    return {
        "task_id": task_id,
        "method": METHOD,
        "mode": mode,
        "score": score,
        "coverage": raw.get("coverage", {}),
        "aCov": raw.get("aCov", 0),
        "sCov": raw.get("sCov", 0),
        "tCov": raw.get("tCov", 0),
        "vCov": raw.get("vCov", 0),
        "l0Checks": raw.get("l0Checks", raw.get("l0_checks", [])),
        "rubric_source": rubric_source,
        "provenance": "index.html",
        "determinate": determinate,
        "error": raw.get("error"),
    }


def write_reward(out_dir: Path, score: float) -> None:
    """Write the main score (0-1) to reward.txt."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "reward.txt").write_text(f"{float(score):.6f}\n", encoding="utf-8")


def summarize(verdicts: list[dict]) -> dict:
    """Aggregate verdicts. Mean over determinate only; skip modes excluded from counts."""
    determinate = [v for v in verdicts if v.get("determinate")]
    indeterminate = [v for v in verdicts
                     if not v.get("determinate") and v.get("mode") not in _SKIP_MODES]
    scores = [float(v["score"]) for v in determinate if v.get("score") is not None]
    mean = round(sum(scores) / len(scores), 6) if scores else 0.0
    modes: dict[str, int] = {}
    sources: dict[str, int] = {}
    for v in verdicts:
        modes[v.get("mode", "?")] = modes.get(v.get("mode", "?"), 0) + 1
        src = v.get("rubric_source") or "none"
        sources[src] = sources.get(src, 0) + 1
    return {
        "trials": len(verdicts),
        "official_mean_score": mean,
        "determinate": len(determinate),
        "indeterminate": len(indeterminate),
        "scores": scores,
        "modes": modes,
        "rubric_sources": sources,
    }
