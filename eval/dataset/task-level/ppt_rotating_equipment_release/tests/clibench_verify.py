#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

VERIFIER_DIR = Path(os.environ.get("CLIBENCH_VERIFIER_DIR", "/logs/verifier"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rubrics", type=Path, required=True)
    parser.add_argument("--rubric-design", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--judge", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--profession", required=True)
    parser.add_argument("--evaluated-model", required=True)
    parser.add_argument("--judge-model", required=True)
    return parser.parse_args()


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return payload


def rubric_items(path: Path) -> list[dict]:
    items = load_json(path).get("rubrics")
    if not isinstance(items, list) or not items:
        raise TypeError(f"Rubric list missing from {path}")
    return items


def has_submission(path: Path) -> bool:
    return path.is_dir() and any(
        item.is_file() and item.name not in {".keep", ".gitkeep"}
        for item in path.rglob("*")
    )


def check_output(path: Path) -> tuple[bool, str]:
    if not path.is_file() or path.stat().st_size == 0:
        return False, "missing_or_empty"
    suffix = path.suffix.lower()
    if suffix in {".pptx", ".xlsx", ".docx"}:
        return (zipfile.is_zipfile(path), "ok" if zipfile.is_zipfile(path) else "invalid_ooxml")
    if suffix == ".html":
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        ok = "<html" in text and "</html>" in text
        return ok, "ok" if ok else "invalid_html_shell"
    if suffix == ".csv":
        try:
            with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
                rows = csv.reader(handle)
                header = next(rows)
            ok = len(header) >= 2
            return ok, "ok" if ok else "invalid_csv_header"
        except Exception:
            return False, "invalid_csv"
    return True, "ok"


def contract_status(output_dir: Path, contract: dict) -> tuple[bool, dict[str, str]]:
    names = contract.get("required_files")
    if not isinstance(names, list) or not names:
        raise ValueError("Invalid task contract")
    statuses: dict[str, str] = {}
    all_ok = True
    for name in names:
        ok, status = check_output(output_dir / str(name))
        statuses[str(name)] = status
        all_ok = all_ok and ok
    return all_ok, statuses


def infrastructure_failures(details: dict) -> int:
    if details.get("empty_submission") is True:
        return 0
    failures = int(bool(details.get("error")))
    rubrics = details.get("rubrics") or []
    for item in rubrics:
        result = item.get("result") or {}
        reasoning = str(result.get("overall_reasoning", ""))
        if reasoning.startswith(("Failed after", "Failed to get")):
            failures += 1
        criteria = result.get("criteria_results") or []
        expected = result.get("criteria_count")
        if isinstance(expected, int) and len(criteria) != expected:
            failures += 1
    expected_rubrics = details.get("total_count")
    if isinstance(expected_rubrics, int) and len(rubrics) != expected_rubrics:
        failures += 1
    return failures


def critical_status(details: dict, design: dict) -> tuple[int, int, list[dict]]:
    critical = [item for item in design.get("criteria_metadata", []) if item.get("critical")]
    lookup: dict[tuple[int, int], bool] = {}
    for rubric in details.get("rubrics") or []:
        ri = rubric.get("index")
        for criterion in (rubric.get("result") or {}).get("criteria_results") or []:
            lookup[(ri, criterion.get("index"))] = criterion.get("passed") is True
    rows = []
    passed = 0
    for item in critical:
        key = (item.get("rubric_index"), item.get("criterion_index"))
        ok = lookup.get(key, False)
        passed += int(ok)
        rows.append({"rubric_index": key[0], "criterion_index": key[1], "rubric_id": item.get("rubric_id"), "passed": ok})
    return passed, len(critical), rows


def write_outputs(
    *,
    args: argparse.Namespace,
    details: dict,
    contract_ok: bool,
    contract_files: dict[str, str],
    status: str,
) -> None:
    verifier = VERIFIER_DIR
    verifier.mkdir(parents=True, exist_ok=True)
    max_score = float(details.get("max_score", 0))
    total_score = float(details.get("total_score", 0))
    normalized = total_score / max_score if max_score else 0.0
    failures = infrastructure_failures(details)
    design = load_json(args.rubric_design)
    critical_passed, critical_total, critical_rows = critical_status(details, design)
    critical_all = critical_total > 0 and critical_passed == critical_total
    gold_gate = normalized >= 0.7 and critical_all and contract_ok and failures == 0
    rewards = {
        "reward": normalized,
        "scored": 1.0,
        "infra_error": float(failures > 0),
        "contract_ok": float(contract_ok),
        "critical_all_pass": float(critical_all),
        "critical_passed": float(critical_passed),
        "critical_total": float(critical_total),
        "gold_gate": float(gold_gate),
    }
    report = {
        "schema_version": "manual-hard6.harbor-score.v1",
        "task_id": args.task_id,
        "profession": args.profession,
        "evaluated_model": args.evaluated_model,
        "judge_model": args.judge_model,
        "status": status,
        "normalized_score": normalized,
        "total_score": total_score,
        "max_score": max_score,
        "contract_ok": contract_ok,
        "contract_files": contract_files,
        "judge_failures": failures,
        "critical_passed": critical_passed,
        "critical_total": critical_total,
        "critical_all_pass": critical_all,
        "critical_results": critical_rows,
        "gold_gate": gold_gate,
        "rubrics": details.get("rubrics", []),
    }
    (verifier / "reward.txt").write_text(f"{normalized:.12g}\n", encoding="utf-8")
    (verifier / "reward.json").write_text(json.dumps(rewards, indent=2) + "\n", encoding="utf-8")
    (verifier / "score_detail.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    rubrics = rubric_items(args.rubrics)
    contract = load_json(args.contract)
    contract_ok, contract_files = contract_status(args.output_dir, contract)
    details_path = VERIFIER_DIR / "judge.json"
    details_path.parent.mkdir(parents=True, exist_ok=True)
    if not has_submission(args.output_dir):
        details = {
            "empty_submission": True,
            "evaluated_model": args.evaluated_model,
            "judge_model": args.judge_model,
            "total_score": 0,
            "max_score": sum(float(item.get("weight", 0)) for item in rubrics),
            "passed_count": 0,
            "total_count": len(rubrics),
            "rubrics": [],
        }
        details_path.write_text(json.dumps(details, indent=2) + "\n")
        write_outputs(args=args, details=details, contract_ok=False, contract_files=contract_files, status="empty_output")
        return 0

    command = [
        sys.executable,
        str(args.judge),
        "--output-dir", str(args.output_dir),
        "--rubrics-file", str(args.rubrics),
        "--details-file", str(details_path),
        "--judge-model", args.judge_model,
        "--max-workers", os.environ.get("CLIBENCH_JUDGE_WORKERS", "3"),
        "--max-retries", os.environ.get("CLIBENCH_JUDGE_MAX_RETRIES", "6"),
        "--timeout-per-rubric", "600",
        "--evaluated-model", args.evaluated_model,
        "--detail-log-dir", str(VERIFIER_DIR / "judge_details"),
        "--detail-log-prefix", args.task_id,
    ]
    completed = subprocess.run(command, env=os.environ.copy(), check=False)
    if completed.returncode != 0 or not details_path.is_file():
        details = {
            "error": f"judge exit={completed.returncode}",
            "evaluated_model": args.evaluated_model,
            "judge_model": args.judge_model,
            "total_score": 0,
            "max_score": sum(float(item.get("weight", 0)) for item in rubrics),
            "passed_count": 0,
            "total_count": len(rubrics),
            "rubrics": [],
        }
        details_path.write_text(json.dumps(details, indent=2) + "\n")
    else:
        details = load_json(details_path)
    status = "judge_infrastructure_error" if infrastructure_failures(details) else ("ok" if contract_ok else "incomplete_submission")
    write_outputs(args=args, details=details, contract_ok=contract_ok, contract_files=contract_files, status=status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
