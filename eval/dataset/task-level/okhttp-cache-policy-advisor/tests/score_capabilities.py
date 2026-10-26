#!/usr/bin/env python3
"""Weighted capability scorer for java_development verifier tests.

The scorer intentionally does not collapse a task to a single binary pass/fail.
It reads a per-task test manifest, maps test case names from a JUnit XML report
to capability weights, and writes a JSON report with:

- overallScore: weighted pass ratio across all manifest cases
- capabilityScores: weighted pass ratio per capability
- level: score band derived from the overall score
- caseResults: individual case outcomes with weights and capabilities

The verifier scripts can still expose overallScore through reward.txt for
systems that expect a single numeric reward.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_BANDS = [
    {"name": "no_signal", "min": 0.0, "max": 0.19},
    {"name": "scaffold_only", "min": 0.20, "max": 0.39},
    {"name": "partial_core", "min": 0.40, "max": 0.59},
    {"name": "core_with_gaps", "min": 0.60, "max": 0.74},
    {"name": "feature_complete", "min": 0.75, "max": 0.89},
    {"name": "robust_development", "min": 0.90, "max": 1.0},
]


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"manifest is not valid JSON: {path}: {exc}") from exc

    if "cases" not in manifest or not isinstance(manifest["cases"], list):
        raise SystemExit("manifest must contain a list field named 'cases'")

    for case in manifest["cases"]:
        for field in ("id", "pytest_name", "capability", "weight"):
            if field not in case:
                raise SystemExit(f"manifest case is missing required field {field!r}: {case}")
        weight = case["weight"]
        if not isinstance(weight, (int, float)) or weight <= 0:
            raise SystemExit(f"manifest case weight must be positive: {case}")

    return manifest


def iter_testcases(junit_path: Path) -> list[ET.Element]:
    try:
        root = ET.parse(junit_path).getroot()
    except FileNotFoundError as exc:
        raise SystemExit(f"JUnit XML not found: {junit_path}") from exc
    except ET.ParseError as exc:
        raise SystemExit(f"JUnit XML is not parseable: {junit_path}: {exc}") from exc

    if root.tag == "testcase":
        return [root]
    return list(root.iter("testcase"))


def junit_outcomes(junit_path: Path) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, dict[str, Any]] = {}
    for testcase in iter_testcases(junit_path):
        name = testcase.attrib.get("name", "")
        classname = testcase.attrib.get("classname", "")
        nodeids = {name}
        if classname:
            nodeids.add(f"{classname}.{name}")

        failed = any(child.tag in {"failure", "error"} for child in testcase)
        skipped = any(child.tag == "skipped" for child in testcase)
        status = "failed" if failed else "skipped" if skipped else "passed"
        record = {
            "status": status,
            "time": float(testcase.attrib.get("time", "0") or 0),
            "classname": classname,
            "name": name,
        }
        for nodeid in nodeids:
            outcomes[nodeid] = record
    return outcomes


def score_band(score: float, bands: list[dict[str, Any]]) -> str:
    rounded = round(score, 10)
    for band in bands:
        if float(band["min"]) <= rounded <= float(band["max"]):
            return str(band["name"])
    return "unclassified"


def score(manifest: dict[str, Any], outcomes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    bands = manifest.get("scoreBands") or DEFAULT_BANDS
    case_results = []
    capability_totals: dict[str, float] = defaultdict(float)
    capability_passed: dict[str, float] = defaultdict(float)
    total = 0.0
    passed = 0.0

    for case in manifest["cases"]:
        case_id = str(case["id"])
        pytest_name = str(case["pytest_name"])
        capability = str(case["capability"])
        weight = float(case["weight"])
        outcome = outcomes.get(pytest_name) or outcomes.get(case_id)
        status = outcome["status"] if outcome else "not_run"
        is_passed = status == "passed"

        total += weight
        capability_totals[capability] += weight
        if is_passed:
            passed += weight
            capability_passed[capability] += weight

        case_results.append(
            {
                "id": case_id,
                "pytestName": pytest_name,
                "capability": capability,
                "category": case.get("category", capability),
                "visibility": case.get("visibility", "hidden"),
                "weight": weight,
                "status": status,
                "passedWeight": weight if is_passed else 0.0,
            }
        )

    capability_scores = {}
    for capability in sorted(capability_totals):
        cap_total = capability_totals[capability]
        cap_score = capability_passed[capability] / cap_total if cap_total else 0.0
        capability_scores[capability] = {
            "score": round(cap_score, 6),
            "level": score_band(cap_score, bands),
            "passedWeight": round(capability_passed[capability], 6),
            "totalWeight": round(cap_total, 6),
        }

    overall = passed / total if total else 0.0
    return {
        "taskId": manifest.get("taskId"),
        "overallScore": round(overall, 6),
        "level": score_band(overall, bands),
        "passedWeight": round(passed, 6),
        "totalWeight": round(total, 6),
        "capabilityScores": capability_scores,
        "caseResults": case_results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("junit_xml", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    outcomes = junit_outcomes(args.junit_xml)
    result = score(manifest, outcomes)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n")
    print(json.dumps({"overallScore": result["overallScore"], "level": result["level"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
