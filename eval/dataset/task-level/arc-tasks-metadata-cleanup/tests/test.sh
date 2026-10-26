#!/usr/bin/env bash
set +e

mkdir -p /logs/verifier
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "${script_dir}/test.sh.harbor-original"
status=$?

if [[ "${status}" -eq 0 ]]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit "${status}"
