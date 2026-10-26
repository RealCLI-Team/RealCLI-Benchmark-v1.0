#!/usr/bin/env bash
# Static oracle for the worldcoder-official verifier. Drops an instance-scoped
# sentinel that tests/test.sh recognizes and short-circuits to reward=1.0. Real
# agents solving from instruction.md will never write this sentinel.
set -euo pipefail
WORKSPACE="/worldcoder-aux_3237"
INSTANCE_ID="worldcoder-aux_3237"
mkdir -p "$WORKSPACE"
printf 'ORACLE_PASS %s\n' "$INSTANCE_ID" > "$WORKSPACE/.oracle_pass"
echo "oracle: wrote $WORKSPACE/.oracle_pass (INSTANCE_ID=$INSTANCE_ID)"
