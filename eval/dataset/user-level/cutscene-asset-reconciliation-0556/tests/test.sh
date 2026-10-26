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

if [ ! -f /app/result.json ]; then
  echo "FAIL: /app/result.json does not exist" >&2
  exit 1
fi
if [ ! -f /app/solution.py ]; then
  echo "FAIL: /app/solution.py does not exist" >&2
  exit 1
fi

python3 - <<'PY'
import json
import os
import shutil
import subprocess
import sys
import tempfile

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


A = "inputs/data/assets"

# ---- main fixture: result.json field-by-field ----
with open("/app/result.json", "r", encoding="utf-8") as f:
    res = json.load(f)

check(res.get("kept") == ["intro/a.playable", "intro/a.prefab", "win/c.playable"],
      "kept mismatch: %r" % res.get("kept"))
check(res.get("deleted") == ["bad/n.playable", "bm/o.playable", "boss/b.playable",
                             "dup/x.playable", "m/m.playable", "stray/z.prefab"],
      "deleted mismatch: %r" % res.get("deleted"))
check(res.get("missing") == [{"id": "e3", "path": "win/c.prefab"}],
      "missing mismatch: %r" % res.get("missing"))
check(res.get("invalid") == [
        {"id": "dup", "reason": "duplicate_id"},
        {"id": "e6", "reason": "mutex_flags"},
        {"id": "e7", "reason": "bad_key"},
        {"id": "e8", "reason": "bad_key"},
        {"id": "e8", "reason": "mutex_flags"},
      ],
      "invalid mismatch: %r" % res.get("invalid"))

# sorted-ness checks
check(res["kept"] == sorted(res["kept"]), "kept not sorted")
check(res["deleted"] == sorted(res["deleted"]), "deleted not sorted")
check(res["missing"] == sorted(res["missing"], key=lambda r: (r["id"], r["path"])),
      "missing not sorted")
check(res["invalid"] == sorted(res["invalid"], key=lambda r: (r["id"], r["reason"])),
      "invalid not sorted")

# ---- Case A: disabled entry's file deleted from disk ----
check(not os.path.exists(os.path.join(A, "boss/b.playable")),
      "Case A: boss/b.playable should be deleted from disk")
check("boss/b.playable" in res["deleted"], "Case A: boss/b.playable not in deleted")

# ---- Case B: hand-authored + .meta survive ----
check(os.path.isfile(os.path.join(A, "hand/keep.playable")),
      "Case B: hand-authored hand/keep.playable must survive")
check("hand/keep.playable" not in res["deleted"],
      "Case B: hand-authored file must not be in deleted")
check(os.path.isfile(os.path.join(A, "intro/a.playable.meta")),
      "Case B: .meta file must survive")
check("intro/a.playable.meta" not in res["deleted"],
      "Case B: .meta must never be deleted/managed")

# ---- Case C: two-way join (missing on disk vs orphan on disk) ----
check(os.path.isfile(os.path.join(A, "win/c.playable")),
      "Case C: win/c.playable (desired, present) must be kept on disk")
check(not os.path.exists(os.path.join(A, "win/c.prefab")),
      "Case C: win/c.prefab was never on disk")
check(not os.path.exists(os.path.join(A, "stray/z.prefab")),
      "Case C: unmanaged-by-config orphan stray/z.prefab must be deleted")

# ---- Case D: invalid records exact ----
check(not os.path.exists(os.path.join(A, "dup/x.playable")),
      "Case D: duplicate-id entry file must be deleted")
check(not os.path.exists(os.path.join(A, "m/m.playable")),
      "Case D: mutex_flags entry file must be deleted")
check(not os.path.exists(os.path.join(A, "bad/n.playable")),
      "Case D: bad_key entry file must be deleted")

# ---- Negative safety: no abs paths / users / urls ----
blob = json.dumps(res)
check("http://" not in blob and "https://" not in blob, "URL found in result.json")
for p in res["kept"] + res["deleted"]:
    check(not p.startswith("/"), "absolute path in result.json: %s" % p)
for r in res["missing"]:
    check(not r["path"].startswith("/"), "absolute path in missing: %s" % r["path"])
check("/home/" not in blob and "/Users/" not in blob and "/root/" not in blob,
      "user path found in result.json")

# ---- Idempotency on main fixture ----
# Keep verifier-created output out of the agent deliverable directory.
import tempfile
with tempfile.TemporaryDirectory(prefix="cutscene_verify_") as verify_tmp:
    result2_path = os.path.join(verify_tmp, "result2.json")
    subprocess.run(
        ["python", "/app/solution.py", "--config", "inputs/data/config.json",
         "--assets", A, "--out", result2_path],
        check=True,
    )
    with open(result2_path, "r", encoding="utf-8") as f:
        res2 = json.load(f)
    check(res2["kept"] == res["kept"], "idempotency: kept changed")
    check(res2["missing"] == res["missing"], "idempotency: missing changed")
    check(res2["invalid"] == res["invalid"], "idempotency: invalid changed")
    check(res2["deleted"] == [], "idempotency: second run must delete nothing: %r" % res2["deleted"])

# ---- Independent fixture with generic reference recomputation ----
MANAGED = (".playable", ".prefab")


def reference(cfg, root):
    entries = cfg["entries"]
    counts = {}
    for e in entries:
        counts[e["id"]] = counts.get(e["id"], 0) + 1
    invalid = set()
    valid = []
    for e in entries:
        reasons = []
        if counts[e["id"]] > 1:
            reasons.append("duplicate_id")
        if not str(e.get("key", "")).startswith("cutscene_"):
            reasons.append("bad_key")
        if e.get("use_timeline") and e.get("use_cutscene"):
            reasons.append("mutex_flags")
        if reasons:
            for r in reasons:
                invalid.add((e["id"], r))
        elif e.get("enabled"):
            valid.append(e)
    desired = set()
    for e in valid:
        for p in e.get("required", []):
            desired.add(p)
    hand = set()
    hp = os.path.join(root, ".handauthored")
    if os.path.isfile(hp):
        for ln in open(hp, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                hand.add(ln)
    managed = set()
    for dp, _dn, fns in os.walk(root):
        for n in fns:
            if n.endswith(MANAGED):
                managed.add(os.path.relpath(os.path.join(dp, n), root).replace(os.sep, "/"))
    kept = sorted(p for p in managed if p in desired)
    deleted = sorted(p for p in managed if p not in desired and p not in hand)
    missing = sorted(
        ({"id": e["id"], "path": p} for e in valid for p in e.get("required", []) if p not in managed),
        key=lambda r: (r["id"], r["path"]),
    )
    inv = sorted(({"id": i, "reason": r} for (i, r) in invalid), key=lambda r: (r["id"], r["reason"]))
    return {"kept": kept, "deleted": deleted, "missing": missing, "invalid": inv}


tmp = tempfile.mkdtemp()
try:
    root = os.path.join(tmp, "assets")
    os.makedirs(root)
    open(os.path.join(root, "a.playable"), "w").write("x")
    open(os.path.join(root, "b.prefab"), "w").write("x")
    open(os.path.join(root, "hand.playable"), "w").write("x")
    open(os.path.join(root, "extra.prefab"), "w").write("x")
    open(os.path.join(root, "note.meta"), "w").write("x")
    open(os.path.join(root, ".handauthored"), "w").write("# keep this\nhand.playable\n")
    cfg = {"entries": [
        {"id": "t1", "key": "cutscene_a", "enabled": True, "use_timeline": False, "use_cutscene": True, "required": ["a.playable"]},
        {"id": "t2", "key": "cutscene_b", "enabled": False, "use_timeline": False, "use_cutscene": True, "required": ["b.prefab"]},
        {"id": "t3", "key": "cutscene_c", "enabled": True, "use_timeline": False, "use_cutscene": True, "required": ["c.playable"]},
    ]}
    cfg_path = os.path.join(tmp, "config.json")
    json.dump(cfg, open(cfg_path, "w"))
    expected = reference(cfg, root)  # computed on pristine state
    outp = os.path.join(tmp, "out.json")
    subprocess.run(["python", "/app/solution.py", "--config", cfg_path, "--assets", root, "--out", outp], check=True)
    got = json.load(open(outp))
    check(got == expected, "independent fixture mismatch:\n got=%r\n exp=%r" % (got, expected))
    check(os.path.isfile(os.path.join(root, "hand.playable")), "indep: hand-authored must survive")
    check(os.path.isfile(os.path.join(root, "note.meta")), "indep: .meta must survive")
    check(not os.path.exists(os.path.join(root, "b.prefab")), "indep: disabled orphan must be deleted")
    check(not os.path.exists(os.path.join(root, "extra.prefab")), "indep: unowned orphan must be deleted")
    check(expected["deleted"] == ["b.prefab", "extra.prefab"], "indep: deleted expected b.prefab,extra.prefab")
    check(expected["missing"] == [{"id": "t3", "path": "c.playable"}], "indep: missing t3 c.playable")
    # idempotency on independent fixture
    out2 = os.path.join(tmp, "out2.json")
    subprocess.run(["python", "/app/solution.py", "--config", cfg_path, "--assets", root, "--out", out2], check=True)
    got2 = json.load(open(out2))
    check(got2["deleted"] == [], "indep idempotency: second run must delete nothing")
    check(got2["kept"] == got["kept"], "indep idempotency: kept changed")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

if FAIL:
    print("TEST FAILURES:")
    for m in FAIL:
        print(" - " + m)
    sys.exit(1)
print("ALL TESTS PASSED")
PY
