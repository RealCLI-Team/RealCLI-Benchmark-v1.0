#!/usr/bin/env bash
set -uo pipefail

python3 /tests/test_outputs.py
status=$?
mkdir -p /logs/verifier
if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
