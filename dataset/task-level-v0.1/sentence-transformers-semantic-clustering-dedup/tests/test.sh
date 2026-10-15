#!/usr/bin/env bash
set -uo pipefail
mkdir -p /logs/verifier
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  python3 -m pytest /tests/test_outputs.py -q -p no:cacheprovider
status=$?
if [ "$status" -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit "$status"
