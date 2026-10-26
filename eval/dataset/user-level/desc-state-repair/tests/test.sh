#!/bin/bash
set -e
cd /app
mkdir -p /logs/verifier

set +e
python3 -m pytest /tests/test_outputs.py -rA
RC=$?
set -e

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT

if [ $RC -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi

exit $RC
