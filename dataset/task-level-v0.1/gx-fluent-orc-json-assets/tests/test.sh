#!/bin/bash
set -u

mkdir -p /logs/verifier

cd /app
python -m pytest /tests/test_outputs.py -q -p no:cacheprovider > /logs/verifier/pytest.log 2>&1
status=$?

if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi

exit "$([ "$status" -eq 0 ] && echo 0 || echo 1)"
