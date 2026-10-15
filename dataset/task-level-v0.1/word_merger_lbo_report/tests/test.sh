#!/bin/bash
set -uo pipefail

# Install verifier dependencies
python3 -m pip install pytest python-docx openpyxl httpx mammoth -q 2>/dev/null || \
pip3 install --break-system-packages pytest python-docx openpyxl httpx mammoth -q 2>/dev/null || true

mkdir -p /logs/verifier

TEST_FILE="/tests/test_outputs.py"
if [ ! -f "$TEST_FILE" ]; then
  TEST_FILE="$(find /tests "$(dirname "$0")" -name test_outputs.py 2>/dev/null | head -1)"
fi
if [ -z "${TEST_FILE:-}" ] || [ ! -f "$TEST_FILE" ]; then
  echo "ERROR: Could not find test_outputs.py" | tee /logs/verifier/test-stdout.txt
  echo "0" > /logs/verifier/reward.txt
  exit 0
fi

# Run pytest
python3 -m pytest "$TEST_FILE" -v --tb=short --color=no > /logs/verifier/test-stdout.txt 2>&1
exit_code=$?
cat /logs/verifier/test-stdout.txt

# Preserve reward.txt if test_outputs.py already wrote it
if [ -f /logs/verifier/reward.txt ]; then
  exit 0
fi

# Fallback
if [ "$exit_code" -eq 0 ]; then
  echo "1" > /logs/verifier/reward.txt
else
  echo "0" > /logs/verifier/reward.txt
fi

exit 0
