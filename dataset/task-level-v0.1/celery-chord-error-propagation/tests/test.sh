#!/usr/bin/env bash
set -uo pipefail
mkdir -p /logs/verifier
cd /
python3 -m pytest /tests/test_outputs.py -q -p no:cacheprovider -o addopts= --timeout=60 --timeout-method=signal > /logs/verifier/pytest.log 2>&1
status=$?
if [ "$status" -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
tail -20 /logs/verifier/pytest.log
exit "$status"
