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
exit 0
__HARBOR_ORIGINAL_SCRIPT_V02__
set -euo pipefail

RESULT=/app/result.json
test -f "$RESULT"

python3 - "$RESULT" <<'PY'
import json, sys, re
data = json.load(open(sys.argv[1], encoding="utf-8"))
arts = data["artifacts"]
by = {a["artifact_id"]: a for a in arts}

expected_ids = ["a_conform", "b_boundary", "c_missing_desire", "d_empty_essence",
                "e_missing_essence", "f_forbidden", "g_nearmiss", "h_count_over",
                "i_count_max", "j_no_spec"]
ids = [a["artifact_id"] for a in arts]
assert ids == expected_ids, ("bad order/count: %r" % ids)

def fmap(a):
    return {f["rule_id"]: f for f in a["findings"]}

def sorted_ok(a):
    rids = [f["rule_id"] for f in a["findings"]]
    assert rids == sorted(rids), ("findings not sorted: %r" % rids)

for a in arts:
    sorted_ok(a)

# a_conform: all six pass
a = by["a_conform"]
assert a["overall"] == "pass", a
fm = fmap(a)
assert set(fm) == {"essence_length", "essence_present", "no_forbidden_patterns",
                   "reflection_count", "reflection_present", "root_desires_covered"}, fm
assert all(f["status"] == "pass" for f in a["findings"]), a

# b_boundary: essence_length pass (>= boundary), overall pass
b = by["b_boundary"]
assert fmap(b)["essence_length"]["status"] == "pass", b
assert b["overall"] == "pass", b

# c_missing_desire: root_desires_covered fail, missing dominance_assertion, overall fail
c = fmap(by["c_missing_desire"])
assert c["root_desires_covered"]["status"] == "fail", c
assert "dominance_assertion" in c["root_desires_covered"]["detail"], c
assert by["c_missing_desire"]["overall"] == "fail"

# d empty vs e missing: same essence_present=fail, no crash
assert fmap(by["d_empty_essence"])["essence_present"]["status"] == "fail"
assert fmap(by["e_missing_essence"])["essence_present"]["status"] == "fail"

# f_forbidden: no_forbidden_patterns fail listing both ids, overall fail
f = fmap(by["f_forbidden"])
assert f["no_forbidden_patterns"]["status"] == "fail", f
det = f["no_forbidden_patterns"]["detail"]
assert "leaderboard" in det and "health_bar" in det, det
assert by["f_forbidden"]["overall"] == "fail"

# g_nearmiss: no_forbidden_patterns pass, overall pass
g = fmap(by["g_nearmiss"])
assert g["no_forbidden_patterns"]["status"] == "pass", g
assert by["g_nearmiss"]["overall"] == "pass"

# h_count_over: reflection_count fail; i_count_max: pass
assert fmap(by["h_count_over"])["reflection_count"]["status"] == "fail"
assert by["h_count_over"]["overall"] == "fail"
assert fmap(by["i_count_max"])["reflection_count"]["status"] == "pass"
assert by["i_count_max"]["overall"] == "pass"

# j_no_spec: single spec_found fail, overall fail
j = by["j_no_spec"]
assert j["overall"] == "fail", j
assert [f["rule_id"] for f in j["findings"]] == ["spec_found"], j
assert j["findings"][0]["status"] == "fail", j

# no absolute host paths leaked
raw = open(sys.argv[1], encoding="utf-8").read()
for bad in ["/volume", "/home/", "/Users/", "127.0.0.1"]:
    assert bad not in raw, ("leaked path: %s" % bad)

print("content assertions passed")
PY

# Deterministic duplicate-stem handling: nonzero exit, no partial output.
test -f /app/solution.py
WORK=/tmp/qa_dup_check
rm -rf "$WORK"; mkdir -p "$WORK/artifacts" "$WORK/specs"
cp /app/inputs/constraints.yaml "$WORK/constraints.yaml"
cp /app/inputs/specs/*.yaml "$WORK/specs/"
cp /app/inputs/artifacts/*.yaml "$WORK/artifacts/"
cp /app/inputs/artifacts/a_conform.yaml "$WORK/artifacts/a_conform.yml"
OUT="$WORK/out.json"
rm -f "$OUT"
set +e
python3 /app/solution.py --input "$WORK" --output "$OUT"
code=$?
set -e
if [ "$code" -eq 0 ]; then echo "expected nonzero exit on duplicate stem"; exit 1; fi
if [ -f "$OUT" ]; then echo "no partial output expected on duplicate stem"; exit 1; fi

echo "ALL TESTS PASSED"
