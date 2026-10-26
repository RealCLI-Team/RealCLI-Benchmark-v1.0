#!/usr/bin/env bash
# Oracle for the implanted interactive verifier.
#
# This benchmark ships no ground-truth artifact, so there is nothing for an
# oracle to build: instead it drops an instance-scoped sentinel that
# tests/test.sh recognizes and short-circuits to reward=1.0. That is what makes
# the harbor op/non-op calibration meaningful — the non-op run leaves the
# workspace empty, writes no sentinel, and is scored 0.0 by the real verifier.
#
# The sentinel is scoped to THIS instance id (and matched on the whole first
# line) so a stray file copied between tasks cannot pass a different one, and a
# real agent solving from instruction.md never writes it.
set -euo pipefail
WORKSPACE="/eddy-current-damping-sim_0100"
INSTANCE_ID="eddy-current-damping-sim_0100"
mkdir -p "$WORKSPACE"
printf 'ORACLE_PASS %s\n' "$INSTANCE_ID" > "$WORKSPACE/.oracle_pass"
echo "oracle: wrote $WORKSPACE/.oracle_pass (INSTANCE_ID=$INSTANCE_ID)"
