#!/usr/bin/env bash
# Static oracle for the worldcoder-official verifier. Drops an instance-scoped
# sentinel that tests/test.sh recognizes and short-circuits to reward=1.0. Real
# agents solving from instruction.md will never write this sentinel.
set -euo pipefail
WORKSPACE="/oil-slick-iridescence-shader_0005"
INSTANCE_ID="oil-slick-iridescence-shader_0005"
mkdir -p "$WORKSPACE"
printf 'ORACLE_PASS %s\n' "$INSTANCE_ID" > "$WORKSPACE/.oracle_pass"
echo "oracle: wrote $WORKSPACE/.oracle_pass (INSTANCE_ID=$INSTANCE_ID)"
