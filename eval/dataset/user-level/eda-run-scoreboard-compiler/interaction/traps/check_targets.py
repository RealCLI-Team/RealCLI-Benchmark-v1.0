#!/usr/bin/env python3
"""Validate result.json against project targets in run_targets.json."""
import json
import os
import sys

RESULT = os.environ.get("RESULT_PATH", "/app/result.json")
TARGETS = os.environ.get("TARGETS_PATH", "/app/inputs/run_targets.json")


def main():
    if not os.path.exists(RESULT):
        print(f"ERROR: {RESULT} not found")
        sys.exit(2)
    if not os.path.exists(TARGETS):
        print(f"ERROR: {TARGETS} not found")
        sys.exit(2)

    result = json.load(open(RESULT))
    targets = json.load(open(TARGETS))

    failures = []
    passes = []

    if "min_scored_count" in targets:
        actual = result["summary"]["scored_count"]
        required = targets["min_scored_count"]
        if actual < required:
            failures.append(
                f"FAIL: min_scored_count: got {actual}, required >= {required}"
            )
        else:
            passes.append(f"PASS: scored_count={actual} meets min={required}")

    if "required_best_run" in targets:
        actual = result["summary"]["best_run_id"]
        required = targets["required_best_run"]
        if actual != required:
            failures.append(
                f"FAIL: required_best_run: got {actual!r}, required {required!r}"
            )
        else:
            passes.append(f"PASS: best_run_id={actual!r}")

    if "min_best_composite" in targets:
        scored = result.get("scored_runs", [])
        if not scored:
            failures.append("FAIL: min_best_composite: no scored runs")
        else:
            actual = scored[0]["composite"]
            required = targets["min_best_composite"]
            if actual < required:
                failures.append(
                    f"FAIL: min_best_composite: best is {actual}, required >= {required}"
                )
            else:
                passes.append(
                    f"PASS: best_composite={actual} meets min={required}"
                )

    for p in passes:
        print(p)
    for f in failures:
        print(f)

    if failures:
        print(f"\n{len(failures)} target(s) not met.")
        sys.exit(1)
    else:
        print("\nAll targets met.")


if __name__ == "__main__":
    main()
