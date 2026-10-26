#!/usr/bin/env bash
# Quick verification helper -- compares result against pre-computed baseline
# Generated: 2026-08-15 from rubric v1.0-draft
BASELINE="/app/.check_cache/expected_baseline.jsonl"
RESULT="${1:-/app/result.jsonl}"

if [ ! -f "$RESULT" ]; then
  echo "ERROR: $RESULT not found"
  exit 1
fi

echo "Comparing $RESULT against cached baseline..."
python3 - "$RESULT" <<'PY'
import json, sys

baseline_path = "/app/.check_cache/expected_baseline.jsonl"
result_path = sys.argv[1] if len(sys.argv) > 1 else "/app/result.jsonl"

baseline = [json.loads(l) for l in open(baseline_path) if l.strip()]
try:
    actual = [json.loads(l) for l in open(result_path) if l.strip()]
except Exception as e:
    print(f"ERROR reading result: {e}")
    sys.exit(1)

if len(baseline) != len(actual):
    print(f"MISMATCH: row count baseline={len(baseline)} vs actual={len(actual)}")
    sys.exit(1)

diffs = 0
for i, (b, a) in enumerate(zip(baseline, actual)):
    for k in sorted(set(list(b.keys()) + list(a.keys()))):
        if b.get(k) != a.get(k):
            print(f"  row {i}: {k} expected {b.get(k)!r} got {a.get(k)!r}")
            diffs += 1

if diffs:
    print(f"MISMATCH: {diffs} field differences found")
    sys.exit(1)

print("MATCH: all rows agree with baseline")
PY
