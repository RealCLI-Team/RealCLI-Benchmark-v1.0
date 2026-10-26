#!/usr/bin/env bash
set -uo pipefail

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT
mkdir -p /logs/verifier
cd /app
python -m pytest /tests/test_outputs.py -q -p no:cacheprovider 2>&1 | tail -80
status=${PIPESTATUS[0]}
if [ "$status" -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit "$status"
