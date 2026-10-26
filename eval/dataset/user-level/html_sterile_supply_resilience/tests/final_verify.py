#!/usr/bin/env python3
"""Manual Hard 6 final verifier with separate content and rendered-VLM judges."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


LOG = Path(os.environ.get("CLIBENCH_VERIFIER_LOG_DIR", "/logs/verifier"))


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(path)
    return value


def check_file(path: Path) -> tuple[bool, str]:
    if not path.is_file() or path.stat().st_size == 0:
        return False, "missing_or_empty"
    suffix = path.suffix.lower()
    if suffix in {".pptx", ".xlsx", ".docx"}:
        ok = zipfile.is_zipfile(path)
        return ok, "ok" if ok else "invalid_ooxml"
    if suffix == ".html":
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        ok = "<html" in text and "</html>" in text
        return ok, "ok" if ok else "invalid_html_shell"
    if suffix == ".csv":
        try:
            with path.open(encoding="utf-8-sig", errors="replace", newline="") as stream:
                header = next(csv.reader(stream))
            ok = len(header) >= 2
            return ok, "ok" if ok else "invalid_csv_header"
        except Exception:
            return False, "invalid_csv"
    return True, "ok"


def failed_result(index: int, rubric: dict, reason: str) -> dict:
    criteria = list(rubric.get("criterion") or [])
    return {
        "index": index,
        "rubric": rubric["rubric"],
        "weight": rubric["weight"],
        "evaluation_class": rubric.get("evaluation_class", "content"),
        "evaluation_mode": rubric.get("evaluation_mode", "text_and_structure_judge"),
        "result": {
            "passed": False,
            "score": 0,
            "criteria_count": len(criteria),
            "criteria_passed": 0,
            "criteria_results": [
                {"index": criterion_index, "criterion": criterion, "passed": False, "reasoning": reason, "evidence": ""}
                for criterion_index, criterion in enumerate(criteria)
            ],
            "overall_reasoning": reason,
        },
    }


def judge_failed(details: dict) -> bool:
    if details.get("error"):
        return True
    for row in details.get("rubrics", []):
        reason = str((row.get("result") or {}).get("overall_reasoning", ""))
        if reason.startswith(("Failed after", "Failed to get", "Judge raised")):
            return True
    return False


def apply_dependency_rules(results: list[dict], rules: list[dict]) -> list[dict]:
    """Fail downstream groups when a declared causal prerequisite failed."""
    by_index = {int(row["index"]): row for row in results}
    applied = []
    changed = True
    while changed:
        changed = False
        for rule in rules:
            target = by_index[int(rule["target"])]
            missing = [
                int(index) for index in rule["requires"]
                if not bool(by_index[int(index)]["result"].get("passed"))
            ]
            if missing and bool(target["result"].get("passed")):
                target["result"]["raw_passed_before_dependency_gate"] = True
                target["result"]["passed"] = False
                target["result"]["score"] = 0
                target["result"]["dependency_gate_passed"] = False
                target["result"]["failed_prerequisite_rubric_indices"] = missing
                target["result"]["overall_reasoning"] = (
                    f"Strict dependency gate failed because prerequisite groups {missing} failed. "
                    + str(rule["reason"])
                )
                applied.append({**rule, "failed_prerequisites": missing})
                changed = True
    return applied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rubrics", type=Path, required=True)
    parser.add_argument("--rubric-design", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--judge", type=Path, required=True)
    parser.add_argument("--render", type=Path, required=True)
    parser.add_argument("--visual-judge", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--profession", required=True)
    parser.add_argument("--evaluated-model", required=True)
    parser.add_argument("--content-judge-model", required=True)
    parser.add_argument("--visual-judge-model", required=True)
    args = parser.parse_args()
    LOG.mkdir(parents=True, exist_ok=True)
    rubrics_document = load(args.rubrics)
    all_rubrics = rubrics_document["rubrics"]
    dependency_rules = list(rubrics_document.get("strict_dependency_rules", []))
    content = [row for row in all_rubrics if row.get("evaluation_class") == "content"]
    visual = [(idx, row) for idx, row in enumerate(all_rubrics) if row.get("evaluation_class") == "visual"]
    if len(all_rubrics) != 18 or len(content) != 15 or len(visual) != 3:
        raise ValueError("final tasks require 15 content and 3 visual rubrics")
    contract = load(args.contract)
    required = [str(name) for name in contract["required_files"]]
    statuses = {name: check_file(args.output_dir / name)[1] for name in required}
    contract_ok = all(status == "ok" for status in statuses.values())
    has_output = args.output_dir.is_dir() and any(p.is_file() and p.name not in {".keep", ".gitkeep"} for p in args.output_dir.rglob("*"))
    infrastructure_errors: list[str] = []
    if not has_output:
        results = [failed_result(idx, row, "No model output was submitted.") for idx, row in enumerate(all_rubrics)]
    else:
        with tempfile.TemporaryDirectory(prefix="hard6-content-rubrics-") as tmp:
            content_rubrics = Path(tmp) / "RUBRICS.json"
            content_rubrics.write_text(json.dumps({"schema_version": "clibench.rubrics.v1", "rubrics": content}, ensure_ascii=False), encoding="utf-8")
            content_details = LOG / "content_judge.json"
            command = [
                sys.executable, str(args.judge), "--output-dir", str(args.output_dir),
                "--rubrics-file", str(content_rubrics), "--details-file", str(content_details),
                "--judge-model", args.content_judge_model, "--max-workers", os.environ.get("CLIBENCH_JUDGE_WORKERS", "6"),
                "--max-retries", os.environ.get("CLIBENCH_JUDGE_MAX_RETRIES", "6"), "--timeout-per-rubric", "600",
                "--evaluated-model", args.evaluated_model, "--detail-log-dir", str(LOG / "content_judge_details"),
                "--detail-log-prefix", args.task_id,
            ]
            completed = subprocess.run(command, env=os.environ.copy(), check=False)
            if completed.returncode or not content_details.is_file():
                infrastructure_errors.append(f"content_judge_exit_{completed.returncode}")
                content_results = [failed_result(idx, row, "Content Judge infrastructure failure.") for idx, row in enumerate(content)]
            else:
                details = load(content_details)
                if judge_failed(details):
                    infrastructure_errors.append("content_judge_infrastructure_error")
                content_results = details.get("rubrics", [])
                if len(content_results) != len(content):
                    infrastructure_errors.append("content_judge_incomplete")
                    content_results = [failed_result(idx, row, "Content Judge coverage incomplete.") for idx, row in enumerate(content)]
                else:
                    # The generic content judge intentionally preserves its legacy
                    # result schema and does not echo our private routing fields.
                    # Restore those fields deterministically from the frozen rubric
                    # order before combining content and rendered-VLM results.
                    for idx, row in enumerate(content_results):
                        row["evaluation_class"] = "content"
                        row["evaluation_mode"] = "text_and_structure_judge"
        dependency_gates_applied = apply_dependency_rules(content_results, dependency_rules)
        primary_name = str(contract["primary"])
        primary = args.output_dir / primary_name
        if statuses.get(primary_name) != "ok":
            visual_results = [failed_result(idx, row, "Primary visual deliverable is missing or invalid.") for idx, row in visual]
        else:
            render_dir = LOG / "rendered_pages"
            render_manifest = LOG / "render_manifest.json"
            visual_details = LOG / "visual_judge_details.json"
            render_command = [sys.executable, str(args.render), "--primary", str(primary), "--render-dir", str(render_dir), "--manifest", str(render_manifest)]
            render_run = subprocess.run(render_command, text=True, capture_output=True, check=False)
            if render_run.returncode:
                infrastructure_errors.append("visual_render_infrastructure_error")
                (LOG / "render_error.log").write_text(render_run.stdout + "\n" + render_run.stderr, encoding="utf-8")
                visual_results = [failed_result(idx, row, "Visual renderer infrastructure failure.") for idx, row in visual]
            else:
                visual_command = [
                    sys.executable, str(args.visual_judge), "--rubrics", str(args.rubrics), "--render-dir", str(render_dir),
                    "--manifest", str(render_manifest), "--details", str(visual_details), "--model", args.visual_judge_model,
                ]
                visual_run = subprocess.run(visual_command, text=True, capture_output=True, env=os.environ.copy(), check=False)
                if visual_run.returncode or not visual_details.is_file():
                    infrastructure_errors.append("visual_judge_infrastructure_error")
                    (LOG / "visual_judge_error.log").write_text(visual_run.stdout + "\n" + visual_run.stderr, encoding="utf-8")
                    visual_results = [failed_result(idx, row, "VLM Judge infrastructure failure.") for idx, row in visual]
                else:
                    visual_results = load(visual_details)["rubrics"]
        results = [*content_results, *visual_results]
    results.sort(key=lambda row: row["index"])
    total_score = sum(float((row.get("result") or {}).get("score", 0)) for row in results)
    design = load(args.rubric_design)
    critical_meta = [row for row in design["criteria_metadata"] if row.get("critical")]
    by_index = {row["index"]: row for row in results}
    critical_rows = []
    for item in critical_meta:
        criterion_index = int(item["criterion_index"])
        criteria_results = by_index.get(item["rubric_index"], {}).get("result", {}).get("criteria_results", [])
        criterion_result = next(
            (row for row in criteria_results if int(row.get("index", -1)) == criterion_index),
            None,
        )
        passed = bool(by_index.get(item["rubric_index"], {}).get("result", {}).get("passed") is True and criterion_result and criterion_result.get("passed") is True)
        critical_rows.append({
            "rubric_index": item["rubric_index"],
            "criterion_index": criterion_index,
            "rubric_id": item["rubric_id"],
            "passed": passed,
        })
    critical_all = len(critical_rows) == 5 and all(row["passed"] for row in critical_rows)
    normalized = total_score / 100.0
    infra = bool(infrastructure_errors)
    # [threshold-cut 默认口径] 用户拍板:normalized>=阈值(且 contract_ok、非 infra)直接门控成 reward=1.0;
    # 低于阈值仍按原连续分 reward=normalized。阈值 0.85。这是评测默认行为,不再用 ORACLE_MODE 门控。
    # 只加分支,不改 rubric/权重/gold_gate/评测口径。
    _oracle_threshold = float(os.environ.get("ORACLE_THRESHOLD", "0.85"))
    _oracle_pass = (normalized >= _oracle_threshold) and contract_ok and not infra
    report = {
        "schema_version": "manual-hard6.final-score.v1", "task_id": args.task_id, "profession": args.profession,
        "evaluated_model": args.evaluated_model, "content_judge_model": args.content_judge_model,
        "visual_judge_model": args.visual_judge_model, "status": "infrastructure_error" if infra else ("ok" if contract_ok else "incomplete_submission"),
        "normalized_score": normalized, "content_score": sum(float(r["result"]["score"]) for r in results if r.get("evaluation_class") == "content") / 70.0,
        "visual_score": sum(float(r["result"]["score"]) for r in results if r.get("evaluation_class") == "visual") / 30.0,
        "total_score": total_score, "max_score": 100, "contract_ok": contract_ok, "contract_files": statuses,
        "infrastructure_errors": infrastructure_errors, "critical_all_pass": critical_all, "critical_results": critical_rows,
        "gold_gate": normalized >= 0.7 and critical_all and contract_ok and not infra, "dependency_gates_applied": dependency_gates_applied if has_output else [], "rubrics": results,
    }
    (LOG / "score_detail.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rewards = {"reward": (1.0 if _oracle_pass else (normalized if not infra else 0.0)), "scored": float(not infra), "infra_error": float(infra), "contract_ok": float(contract_ok), "critical_all_pass": float(critical_all), "gold_gate": float(report["gold_gate"]), "content_score": report["content_score"], "visual_score": report["visual_score"]}
    (LOG / "reward.json").write_text(json.dumps(rewards, indent=2) + "\n", encoding="utf-8")
    (LOG / "reward.txt").write_text(f"{rewards['reward']:.12g}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
