#!/usr/bin/env bash
export CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup PATH=/usr/local/cargo/bin:$PATH

set -uo pipefail

python3 -m pytest /tests/test_outputs.py -v
status=$?
mkdir -p /logs/verifier
if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
