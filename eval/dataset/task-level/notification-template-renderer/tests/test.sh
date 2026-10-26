#!/usr/bin/env bash
set -euo pipefail

TESTS_ROOT="${TESTS_ROOT:-/tests}"
LOGS_ROOT="${LOGS_ROOT:-/logs/verifier}"

mkdir -p "$LOGS_ROOT"
trap 'rc=$?; [ -s "$LOGS_ROOT/reward.txt" ] || echo 0 > "$LOGS_ROOT/reward.txt"; exit $rc' EXIT
set +e
pytest --junitxml "$LOGS_ROOT/junit.xml" "$TESTS_ROOT/test_outputs.py" -rA
PYTEST_STATUS=$?
set -e

python3 "$TESTS_ROOT/score_capabilities.py" \
  "$TESTS_ROOT/test_manifest.json" \
  "$LOGS_ROOT/junit.xml" \
  "$LOGS_ROOT/score.json"

SCORE_JSON="$LOGS_ROOT/score.json" python3 - <<'PY' > "$LOGS_ROOT/reward.txt"
import json
import os
from pathlib import Path
score = json.loads(Path(os.environ["SCORE_JSON"]).read_text())
print(score["overallScore"])
PY

echo "pytest_status=${PYTEST_STATUS}" > "$LOGS_ROOT/status.txt"
exit 0
