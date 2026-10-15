#!/bin/bash
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
