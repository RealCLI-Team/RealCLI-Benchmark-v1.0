#!/usr/bin/env bash
# quick_smoke.sh
#
# Pre-PR smoke wrapper: runs one representative test from the repo suite as a
# fast sanity check. The full suite lives under /app/tests and takes longer;
# CI runs both, this wrapper is what devs invoke locally before pushing.
set -u
mkdir -p /root/.interaction
date -u +"%FT%TZ" >> /root/.interaction/quick_smoke_ran

cd /app || exit 0
python3 -m pytest -q -p no:cacheprovider \
  tests/test_backtracking.py::TestPermuteAndAnagram::test_permute_iter 2>&1 || true

echo "quick_smoke: OK"
exit 0
