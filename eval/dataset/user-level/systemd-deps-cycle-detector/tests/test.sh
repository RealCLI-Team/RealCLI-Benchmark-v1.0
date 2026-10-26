#!/usr/bin/env bash
set +e

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT

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
