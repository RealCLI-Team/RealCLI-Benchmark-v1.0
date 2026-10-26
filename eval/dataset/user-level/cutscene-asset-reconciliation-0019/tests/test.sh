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
set -euo pipefail
cd /app

RES=/app/result.json
ASSETS=/app/inputs/assets

test -f "$RES" || { echo "FAIL: $RES missing"; exit 1; }

python3 - "$RES" "$ASSETS" <<'PY'
import json, os, sys, re
res_path, assets = sys.argv[1], sys.argv[2]
with open(res_path, encoding="utf-8") as f:
    r = json.load(f)

def fail(m):
    print("FAIL:", m); sys.exit(1)

exp_kept = ["alpha/clip.playable", "alpha/rig.prefab", "beta/scene.playable"]
exp_deleted = ["bad/x.playable", "dup/thing.playable", "gamma/old.playable",
               "gamma/old.prefab", "mutex/m.playable", "orphan/stray.prefab"]
exp_missing = [{"id": "e_delta", "path": "delta/missing.playable"}]
exp_invalid = [{"id": "e_badkey", "reason": "bad_key"},
               {"id": "e_dup", "reason": "duplicate_id"},
               {"id": "e_mutex", "reason": "mutex_flags"}]

if r.get("kept") != exp_kept: fail(f"kept mismatch: {r.get('kept')}")
if r.get("deleted") != exp_deleted: fail(f"deleted mismatch: {r.get('deleted')}")
if r.get("missing") != exp_missing: fail(f"missing mismatch: {r.get('missing')}")
if r.get("invalid") != exp_invalid: fail(f"invalid mismatch: {r.get('invalid')}")

# sorting checks
if r["kept"] != sorted(r["kept"]): fail("kept not sorted")
if r["deleted"] != sorted(r["deleted"]): fail("deleted not sorted")

# disk state: kept present
for p in exp_kept:
    if not os.path.isfile(os.path.join(assets, p)): fail(f"kept file missing on disk: {p}")
# deleted absent
for p in exp_deleted:
    if os.path.exists(os.path.join(assets, p)): fail(f"deleted file still on disk: {p}")
# hand-authored + non-managed survive (Case B)
for p in ["manual/handmade.playable", "manual/note.meta"]:
    if not os.path.isfile(os.path.join(assets, p)): fail(f"protected file removed: {p}")
if "manual/handmade.playable" in r["deleted"]: fail("hand-authored in deleted")

# negative-safety on report text
raw = open(res_path, encoding="utf-8").read()
for tok in ["http://", "https://", "zhiheng", "/app", "/home", "/volume"]:
    if tok in raw: fail(f"forbidden token in report: {tok}")
for lst in (r["kept"], r["deleted"]):
    for p in lst:
        if p.startswith("/"): fail(f"absolute path in report: {p}")

print("content-checks OK")
PY

# Idempotency: further runs must be byte-identical and delete nothing.
python3 /app/solution.py --config /app/inputs/config.json --assets "$ASSETS" --out /app/_r2.json
BEFORE=$(find "$ASSETS" -type f | sort)
python3 /app/solution.py --config /app/inputs/config.json --assets "$ASSETS" --out /app/_r3.json
AFTER=$(find "$ASSETS" -type f | sort)

if ! cmp -s /app/_r2.json /app/_r3.json; then
    echo "FAIL: reruns not byte-identical"; exit 1
fi
if [ "$BEFORE" != "$AFTER" ]; then
    echo "FAIL: rerun deleted files"; exit 1
fi
python3 - <<'PY'
import json, sys
r = json.load(open("/app/_r2.json", encoding="utf-8"))
if r["deleted"] != []:
    print("FAIL: rerun deleted list not empty:", r["deleted"]); sys.exit(1)
if r["kept"] != ["alpha/clip.playable", "alpha/rig.prefab", "beta/scene.playable"]:
    print("FAIL: rerun kept changed:", r["kept"]); sys.exit(1)
print("idempotency OK")
PY

rm -f /app/_r2.json /app/_r3.json
echo "ALL TESTS PASSED"
