#!/bin/bash
set -e
cd /app
mkdir -p /logs/verifier

set +e
python3 -m pytest /tests/test_outputs.py -rA
RC=$?
set -e

if [ $RC -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi

exit $RC
