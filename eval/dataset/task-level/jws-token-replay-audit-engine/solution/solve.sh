#!/usr/bin/env bash
set -euo pipefail
cd /app

mkdir -p /app

cat > /app/solution.py << 'PYEOF'
#!/usr/bin/env python3
"""Compact JWS token reverse-audit and replay engine."""
import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os

DIGESTS = {"HS256": hashlib.sha256, "HS512": hashlib.sha512}


def b64url_decode(seg):
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg.encode("ascii") + pad.encode("ascii"))


def load_keys(path):
    with open(path) as f:
        data = json.load(f)
    out = {}
    for k in data.get("keys", []):
        out[k["kid"]] = {"alg": k["alg"],
                         "secret": b64url_decode(k["secret_b64url"])}
    return out


def decode_header_payload(token):
    parts = token.split(".")
    if len(parts) != 3 or any(len(p) == 0 for p in parts):
        return None
    h_seg, p_seg, s_seg = parts
    try:
        header = json.loads(b64url_decode(h_seg).decode("utf-8"))
        payload = json.loads(b64url_decode(p_seg).decode("utf-8"))
        sig = b64url_decode(s_seg)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return None
    signing_input = (h_seg + "." + p_seg).encode("ascii")
    return header, payload, signing_input, sig


def classify(rec, keys, policy):
    token = rec.get("token", "")
    decoded = decode_header_payload(token)
    if decoded is None:
        return {"kid": None, "alg": None, "reason": "MALFORMED", "jti": None}
    header, payload, signing_input, sig = decoded
    kid = header.get("kid")
    kid = kid if isinstance(kid, str) else None
    alg = header.get("alg")
    alg = alg if isinstance(alg, str) else None
    jti = payload.get("jti")
    jti = jti if isinstance(jti, str) else None

    if "iat" in payload:
        iat = payload["iat"]
        if isinstance(iat, bool) or not isinstance(iat, int):
            return {"kid": kid, "alg": alg, "reason": "MALFORMED", "jti": None}
    else:
        iat = None

    if alg is not None and alg.lower() == "none":
        return {"kid": kid, "alg": alg, "reason": "ALG_NONE", "jti": jti}

    if alg not in policy["allowed_algs"]:
        return {"kid": kid, "alg": alg, "reason": "UNSUPPORTED_ALG", "jti": jti}

    if policy["require_kid"] and "kid" not in header:
        return {"kid": kid, "alg": alg, "reason": "MISSING_KID", "jti": jti}

    if kid not in keys:
        return {"kid": kid, "alg": alg, "reason": "UNKNOWN_KID", "jti": jti}

    key = keys[kid]

    if alg != key["alg"]:
        return {"kid": kid, "alg": alg, "reason": "ALG_MISMATCH", "jti": jti}

    if iat is not None and abs(policy["as_of"] - iat) > policy["tolerance_seconds"]:
        return {"kid": kid, "alg": alg, "reason": "EXPIRED", "jti": jti}

    expected = hmac.new(key["secret"], signing_input, DIGESTS[alg]).digest()
    if not hmac.compare_digest(expected, sig):
        return {"kid": kid, "alg": alg, "reason": "BAD_SIGNATURE", "jti": jti}

    return {"kid": kid, "alg": alg, "reason": "OK", "jti": jti}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    inp = args.input
    keys = load_keys(os.path.join(inp, "keys.json"))
    with open(os.path.join(inp, "policy.json")) as f:
        policy = json.load(f)

    records = []
    with open(os.path.join(inp, "tokens.jsonl")) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue

    classified = {}
    for rec in records:
        classified[rec["id"]] = classify(rec, keys, policy)

    consumed = set()
    final = {}
    for rec in sorted(records, key=lambda r: (r["received_at"], r["id"])):
        rid = rec["id"]
        c = classified[rid]
        reason = c["reason"]
        if reason == "OK":
            jti = c["jti"]
            if jti is not None and jti in consumed:
                reason = "REPLAY"
            elif jti is not None:
                consumed.add(jti)
        verdict = "valid" if reason == "OK" else "invalid"
        final[rid] = {"id": rid, "verdict": verdict, "reason": reason,
                      "kid": c["kid"], "alg": c["alg"]}

    results = [final[k] for k in sorted(final.keys())]
    by_reason = {}
    valid = invalid = 0
    for r in results:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
        if r["verdict"] == "valid":
            valid += 1
        else:
            invalid += 1

    report = {
        "summary": {"total": len(results), "valid": valid, "invalid": invalid,
                    "by_reason": by_reason},
        "results": results,
    }
    with open(args.output, "w") as f:
        f.write(json.dumps(report, sort_keys=True, indent=2))
        f.write("\n")


if __name__ == "__main__":
    main()
PYEOF

python3 /app/solution.py --input /app/inputs --output /app/result.json
