#!/usr/bin/env bash
set -uo pipefail
mkdir -p /logs/verifier
cd /tests
python3 -m pytest test_outputs.py -q -p no:cacheprovider 2>&1 | tail -40
status=${PIPESTATUS[0]}
if [ "$status" -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit "$status"
