#!/bin/bash
set -uo pipefail

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT

python3 -m pip install pytest openpyxl tqdm -q 2>/dev/null || pip3 install --break-system-packages pytest openpyxl tqdm -q 2>/dev/null || true
mkdir -p /logs/verifier

TEST_FILE="$(dirname "$0")/test_outputs.py"
[ -f "$TEST_FILE" ] || TEST_FILE="/tests/test_outputs.py"

python3 -m pytest "$TEST_FILE" -v --tb=short --color=no > /logs/verifier/test-stdout.txt 2>&1
exit_code=$?
cat /logs/verifier/test-stdout.txt

# test_outputs.py writes reward.txt + reward_components.json via atexit. Preserve if present.
if [ -f /logs/verifier/reward.txt ]; then
  REWARD=$(cat /logs/verifier/reward.txt)
else
  if [ "$exit_code" -eq 0 ]; then REWARD="1.0"; else REWARD="0.0"; fi
  echo "$REWARD" > /logs/verifier/reward.txt
fi

exit 0
