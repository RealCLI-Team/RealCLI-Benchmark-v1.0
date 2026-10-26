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
set -uo pipefail
cd /app

RESULT=/app/result.json
if [ ! -f "$RESULT" ]; then
    echo "FAIL: $RESULT does not exist"
    exit 1
fi

python3 - << 'PYEOF'
import base64, binascii, hashlib, hmac, json, os, sys

INP = "/app/inputs"
DIGESTS = {"HS256": hashlib.sha256, "HS512": hashlib.sha512}
failures = []
def check(cond, msg):
    if not cond:
        failures.append(msg)

def b64u(seg):
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg.encode() + pad.encode())

# ---- independent reference recomputation ----
with open(os.path.join(INP, "keys.json")) as f:
    kd = json.load(f)
keys = {k["kid"]: {"alg": k["alg"], "secret": b64u(k["secret_b64url"])}
        for k in kd["keys"]}
with open(os.path.join(INP, "policy.json")) as f:
    policy = json.load(f)
recs = []
with open(os.path.join(INP, "tokens.jsonl")) as f:
    for line in f:
        line = line.strip()
        if line:
            recs.append(json.loads(line))

def decode(tok):
    parts = tok.split(".")
    if len(parts) != 3 or any(len(p) == 0 for p in parts):
        return None
    try:
        h = json.loads(b64u(parts[0]).decode("utf-8"))
        p = json.loads(b64u(parts[1]).decode("utf-8"))
        s = b64u(parts[2])
    except Exception:
        return None
    if not isinstance(h, dict) or not isinstance(p, dict):
        return None
    return h, p, (parts[0] + "." + parts[1]).encode("ascii"), s

def classify(rec):
    d = decode(rec.get("token", ""))
    if d is None:
        return "MALFORMED", None, None, None
    h, p, si, sig = d
    kid = h.get("kid") if isinstance(h.get("kid"), str) else None
    alg = h.get("alg") if isinstance(h.get("alg"), str) else None
    jti = p.get("jti") if isinstance(p.get("jti"), str) else None
    if "iat" in p:
        iat = p["iat"]
        if isinstance(iat, bool) or not isinstance(iat, int):
            return "MALFORMED", kid, alg, None
    else:
        iat = None
    if alg is not None and alg.lower() == "none":
        return "ALG_NONE", kid, alg, jti
    if alg not in policy["allowed_algs"]:
        return "UNSUPPORTED_ALG", kid, alg, jti
    if policy["require_kid"] and "kid" not in h:
        return "MISSING_KID", kid, alg, jti
    if kid not in keys:
        return "UNKNOWN_KID", kid, alg, jti
    key = keys[kid]
    if alg != key["alg"]:
        return "ALG_MISMATCH", kid, alg, jti
    if iat is not None and abs(policy["as_of"] - iat) > policy["tolerance_seconds"]:
        return "EXPIRED", kid, alg, jti
    exp = hmac.new(key["secret"], si, DIGESTS[alg]).digest()
    if not hmac.compare_digest(exp, sig):
        return "BAD_SIGNATURE", kid, alg, jti
    return "OK", kid, alg, jti

pre = {r["id"]: classify(r) for r in recs}
consumed = set()
expected = {}
for r in sorted(recs, key=lambda r: (r["received_at"], r["id"])):
    reason, kid, alg, jti = pre[r["id"]]
    if reason == "OK":
        if jti is not None and jti in consumed:
            reason = "REPLAY"
        elif jti is not None:
            consumed.add(jti)
    expected[r["id"]] = {
        "id": r["id"],
        "verdict": "valid" if reason == "OK" else "invalid",
        "reason": reason, "kid": kid, "alg": alg,
    }

# ---- load produced report ----
with open("/app/result.json") as f:
    rep = json.load(f)

check("summary" in rep and "results" in rep, "report missing summary/results")
results = rep.get("results", [])
summary = rep.get("summary", {})

# results sorted by id ascending (strict)
ids = [r["id"] for r in results]
check(ids == sorted(ids), "results not sorted by id ascending")
check(len(ids) == len(set(ids)), "duplicate ids in results")

# per-record verdict/reason/kid/alg match reference
by_id = {r["id"]: r for r in results}
check(set(by_id.keys()) == set(expected.keys()),
      "result id set mismatch: %s vs %s" % (sorted(by_id), sorted(expected)))
for rid, exp in expected.items():
    got = by_id.get(rid, {})
    for field in ("verdict", "reason", "kid", "alg"):
        check(got.get(field) == exp[field],
              "record %s field %s: got %r expected %r" % (rid, field, got.get(field), exp[field]))

# summary counts
total = summary.get("total")
valid = summary.get("valid")
invalid = summary.get("invalid")
br = summary.get("by_reason", {})
check(total == len(results), "summary.total != len(results)")
check(valid + invalid == total, "valid+invalid != total")
check(sum(br.values()) == total, "sum(by_reason) != total")
ref_valid = sum(1 for e in expected.values() if e["verdict"] == "valid")
check(valid == ref_valid, "summary.valid got %r expected %r" % (valid, ref_valid))
ref_br = {}
for e in expected.values():
    ref_br[e["reason"]] = ref_br.get(e["reason"], 0) + 1
check(br == ref_br, "by_reason mismatch: got %r expected %r" % (br, ref_br))
# every reason with count>=1 present, none zero
for k, v in br.items():
    check(v >= 1, "by_reason has non-positive count for %s" % k)

# ---- targeted template assertions ----
# OK HS256 and HS512 present
ok_algs = {by_id[i]["alg"] for i, e in expected.items() if e["reason"] == "OK"}
check("HS256" in ok_algs, "no OK HS256 token")
check("HS512" in ok_algs, "no OK HS512 token")

# replay: t01 OK, t03 REPLAY (same jti, t03 later)
check(by_id["t01"]["reason"] == "OK", "t01 should be OK (replay winner)")
check(by_id["t03"]["reason"] == "REPLAY", "t03 should be REPLAY")
# winner has smaller (received_at, id)
rmap = {r["id"]: r["received_at"] for r in recs}
check((rmap["t01"], "t01") < (rmap["t03"], "t03"), "replay winner ordering wrong")

# legal-empty vs illegal-missing iat
check(by_id["t13"]["reason"] == "OK", "t13 missing iat should be OK not EXPIRED")
check(by_id["t09"]["reason"] == "EXPIRED", "t09 should be EXPIRED")
check(by_id["t12"]["reason"] == "MALFORMED", "t12 non-integer iat should be MALFORMED")

# specific reason coverage
check(by_id["t04"]["reason"] == "ALG_NONE", "t04 ALG_NONE")
check(by_id["t05"]["reason"] == "UNSUPPORTED_ALG", "t05 UNSUPPORTED_ALG")
check(by_id["t06"]["reason"] == "MISSING_KID", "t06 MISSING_KID")
check(by_id["t07"]["reason"] == "UNKNOWN_KID", "t07 UNKNOWN_KID")
check(by_id["t08"]["reason"] == "ALG_MISMATCH", "t08 ALG_MISMATCH")
check(by_id["t10"]["reason"] == "BAD_SIGNATURE", "t10 BAD_SIGNATURE")
check(by_id["t11"]["reason"] == "MALFORMED", "t11 garbage MALFORMED")

# all ten reasons appear at least once
for reason in ["OK", "MALFORMED", "ALG_NONE", "UNSUPPORTED_ALG", "MISSING_KID",
               "UNKNOWN_KID", "ALG_MISMATCH", "EXPIRED", "BAD_SIGNATURE", "REPLAY"]:
    check(reason in br, "reason %s not present in by_reason" % reason)

if failures:
    print("TEST FAILURES:")
    for m in failures:
        print("  -", m)
    sys.exit(1)
print("content assertions passed")
PYEOF
rc=$?
if [ $rc -ne 0 ]; then
    exit 1
fi

# rerun byte-identical determinism check (write to a separate file, keep result.json)
python3 /app/solution.py --input /app/inputs --output /app/_rerun.json
if ! cmp -s /app/result.json /app/_rerun.json; then
    echo "FAIL: rerun output is not byte-identical"
    rm -f /app/_rerun.json
    exit 1
fi
rm -f /app/_rerun.json

echo "PASS"
exit 0
