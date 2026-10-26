#!/usr/bin/env bash
# Wrapped by allinone verifier_contract adapter: run the original test logic, then
# translate its exit code into harbor's /logs/verifier/reward.txt contract.
set +e

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT
_orig_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Harbor uploads task tests to /tests at verifier time. Some synthesized tests
# use /app/tests as their runtime anchor, so expose the same tests at both paths.
if [ "$_orig_dir" = "/tests" ]; then
  mkdir -p /app 2>/dev/null || true
  rm -rf /app/tests 2>/dev/null || true
  ln -s /tests /app/tests 2>/dev/null || cp -a /tests /app/tests 2>/dev/null || true
fi
bash "$_orig_dir/_orig_test.sh"
_rc=$?
_verifier_dir="${HARBOR_VERIFIER_DIR:-/logs/verifier}"
mkdir -p "$_verifier_dir" 2>/dev/null || true
if [ "$_rc" -eq 0 ]; then echo 1 > "$_verifier_dir/reward.txt"; else echo 0 > "$_verifier_dir/reward.txt"; fi
exit "$_rc"
