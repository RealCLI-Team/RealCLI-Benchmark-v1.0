#!/bin/bash

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT
# 判分入口:运行契约检查,结果写入 /logs/verifier/reward.txt
mkdir -p /logs/verifier
cd /app
export PYTHONPATH=/app
if python3 -m pytest -c /dev/null -p no:cacheprovider -q /tests/test_outputs.py \
        > /logs/verifier/pytest.log 2>&1; then
    echo 1 > /logs/verifier/reward.txt
    exit 0
else
    echo 0 > /logs/verifier/reward.txt
    exit 1
fi
