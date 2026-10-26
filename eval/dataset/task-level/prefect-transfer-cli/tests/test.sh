#!/usr/bin/env bash
# Verifier exec strips image ENV, so PATH lacks the prefect venv; restore it
# for `shutil.which("prefect")` (PREFECT_BIN) and the venv python.
export PATH="/app/.venv/bin:${PATH}"
set -uo pipefail

python3 -m pytest /tests/test_outputs.py -v
status=$?
mkdir -p /logs/verifier
if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
