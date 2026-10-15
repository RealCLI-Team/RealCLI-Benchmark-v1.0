#!/usr/bin/env bash
export PYTHONPATH=/app:/app/client/python

mkdir -p /logs/verifier
cd /app
export GRADIO_ANALYTICS_ENABLED=False
python -m pytest /tests/test_outputs.py -q -p no:cacheprovider > /logs/verifier/pytest.log 2>&1
if [ $? -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
    exit 0
else
    echo 0 > /logs/verifier/reward.txt
    exit 1
fi
