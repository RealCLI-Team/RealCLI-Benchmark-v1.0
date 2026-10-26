#!/usr/bin/env bash
# HARBOR_REWARD_WRAPPER_V02
set +e
mkdir -p /logs/verifier
_harbor_inner_script_v02="$(mktemp)"
sed '1,/^__HARBOR_ORIGINAL_SCRIPT_V02__$/d' "$0" > "$_harbor_inner_script_v02"
bash "$_harbor_inner_script_v02"
_harbor_rc_v02=$?
rm -f "$_harbor_inner_script_v02"
if [ "$_harbor_rc_v02" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
# CliBench: collect interaction evidence after the original verifier has
# already decided reward. Never allowed to change the verdict.
if [ -f /tests/collect_evidence.sh ]; then
  bash /tests/collect_evidence.sh || true
fi

exit 0
__HARBOR_ORIGINAL_SCRIPT_V02__
set -uo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }

PROOF=/app/proof.v

# 1. The completed proof file must exist.
[ -f "$PROOF" ] || fail "$PROOF does not exist"

# 2. Theorem statement fingerprint must be unchanged (whitespace-insensitive).
EXPECTED="Theorem mul_add_distr_l : forall n m p : nat, n * (m + p) = n * m + n * p."
norm() { echo "$1" | tr -s ' \t' ' ' | sed -e 's/^ *//' -e 's/ *$//'; }

LINE=$(grep -m1 'Theorem[[:space:]]\+mul_add_distr_l' "$PROOF" || true)
[ -n "$LINE" ] || fail "theorem 'mul_add_distr_l' not found in $PROOF"
if [ "$(norm "$LINE")" != "$(norm "$EXPECTED")" ]; then
  fail "theorem statement was modified:
  got:      $(norm "$LINE")
  expected: $(norm "$EXPECTED")"
fi

# 3. No escape-hatch keywords (as whole words).
if grep -Eqw 'admit|Admitted|Axiom|Parameter|Abort' "$PROOF"; then
  fail "forbidden keyword (admit/Admitted/Axiom/Parameter/Abort) present in $PROOF"
fi

# The proof must be closed with Qed.
grep -Eq '\bQed\.' "$PROOF" || fail "proof is not closed with Qed."

# 4. Recompile from scratch: coqc must exit 0 and produce proof.vo.
cd /app
rm -f /app/proof.vo /app/proof.glob
coqc proof.v || fail "coqc proof.v did not exit 0"
[ -f /app/proof.vo ] || fail "/app/proof.vo was not produced"

# 5. Kernel check must pass.
coqchk -silent -norec proof || fail "coqchk -silent proof did not exit 0"

echo "PASS"
