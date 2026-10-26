#!/usr/bin/env bash
# Static oracle for the worldcoder-official verifier. Drops an instance-scoped
# sentinel that tests/test.sh recognizes and short-circuits to reward=1.0. Real
# agents solving from instruction.md will never write this sentinel.
set -euo pipefail
WORKSPACE="/archery-target-practice-3d_0000"
INSTANCE_ID="archery-target-practice-3d_0000"
mkdir -p "$WORKSPACE"
printf 'ORACLE_PASS %s\n' "$INSTANCE_ID" > "$WORKSPACE/.oracle_pass"
echo "oracle: wrote $WORKSPACE/.oracle_pass (INSTANCE_ID=$INSTANCE_ID)"
