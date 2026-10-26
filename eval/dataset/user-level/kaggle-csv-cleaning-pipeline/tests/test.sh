#!/usr/bin/env bash
set +e

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT

mkdir -p /logs/verifier
uploaded_tests_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
task_root="${TASK_ROOT:-$(pwd)}"
task_tests_dir="${task_root%/}/tests"

# Harbor uploads tests to /tests and runs bash /tests/test.sh while the
# container's current directory remains the task workdir (/app, /workspace, ...).
# Most generated verifiers expect tests/ to live under that workdir, so mirror
# the uploaded verifier bundle there before invoking the original script.
if [[ "${uploaded_tests_dir}" != "${task_tests_dir}" ]]; then
  rm -rf "${task_tests_dir}"
  mkdir -p "${task_tests_dir}"
  cp -a "${uploaded_tests_dir}/." "${task_tests_dir}/"
fi

cd "${task_root}"
bash "${task_tests_dir}/test.sh.harbor-original"
status=$?

if [[ "${status}" -eq 0 ]]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

exit "${status}"
