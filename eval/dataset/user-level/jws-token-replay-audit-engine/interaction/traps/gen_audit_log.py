#!/usr/bin/env python3
"""Generate the previous audit log for cross-reference testing."""
import base64, hashlib, hmac, json, os

INP = "/app/inputs"
DIGESTS = {"HS256": hashlib.sha256, "HS512": hashlib.sha512}

def b64u(seg):
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg.encode() + pad.encode())

with open(os.path.join(INP, "keys.json")) as f:
    kd = json.load(f)
keys = {k["kid"]: {"alg": k["alg"], "secret": b64u(k["secret_b64url"])} for k in kd["keys"]}

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

print("=== JWS Token Audit Report ===")
print("Run ID: audit-20241201-143022")
print("Timestamp: 2024-12-01T14:30:22Z")
print("Engine: jws-audit-v2.3.1")
print(f"Config: input={INP}, tolerance={policy['tolerance_seconds']}s")
print(f"Keys loaded: {len(keys)} ({', '.join(k + ':' + v['alg'] for k, v in keys.items())})")
print(f"Tokens loaded: {len(recs)}")
print(f"Processing order: (received_at, id) ascending")
print(f"Reference time (as_of): {policy['as_of']}")
print()
print("--- Detailed Processing Log ---")
print()

consumed = set()
results = {}
order = sorted(recs, key=lambda r: (r["received_at"], r["id"]))

for idx, rec in enumerate(order):
    tid = rec["id"]
    ts = f"14:30:22.{100 + idx * 7:03d}"
    tok = rec.get("token", "")
    print(f"[{ts}] {'=' * 10} Processing {tid} (received_at={rec['received_at']}) {'=' * 10}")
    print(f"[{ts}]   Token length: {len(tok)} chars")

    d = decode(tok)
    if d is None:
        parts = tok.split(".")
        print(f"[{ts}]   Segments found: {len(parts)} (expected 3 non-empty)")
        if len(parts) >= 1:
            seg0 = parts[0][:50]
            print(f"[{ts}]   Segment[0] preview: {seg0}")
        print(f"[{ts}]   Decode attempt: FAILED — not valid JWS structure")
        print(f"[{ts}]   Rule 1 MALFORMED: MATCH")
        print(f"[{ts}]   => verdict=invalid, reason=MALFORMED, kid=null, alg=null")
        results[tid] = ("invalid", "MALFORMED", None, None)
        print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
        print(f"[{ts}]   Heap: 12.{idx}MB | GC collections: 0")
        print()
        continue

    h, p, si, sig = d
    kid = h.get("kid") if isinstance(h.get("kid"), str) else None
    alg = h.get("alg") if isinstance(h.get("alg"), str) else None
    jti = p.get("jti") if isinstance(p.get("jti"), str) else None

    print(f"[{ts}]   Header: {json.dumps(h, sort_keys=True)}")
    pkeys = ", ".join(sorted(p.keys())) if p else "(empty)"
    print(f"[{ts}]   Payload keys: {pkeys}")

    if "iat" in p:
        iat_raw = p["iat"]
        if isinstance(iat_raw, bool) or not isinstance(iat_raw, int):
            print(f"[{ts}]   iat value: {repr(iat_raw)} (type={type(iat_raw).__name__})")
            print(f"[{ts}]   Rule 1 MALFORMED: MATCH — iat present but not integer")
            print(f"[{ts}]   => verdict=invalid, reason=MALFORMED, kid={kid}, alg={alg}")
            results[tid] = ("invalid", "MALFORMED", kid, alg)
            print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
            print(f"[{ts}]   Heap: 12.{idx}MB | GC collections: 0")
            print()
            continue
        iat = iat_raw
        print(f"[{ts}]   iat={iat}, jti={jti or '-'}")
    else:
        iat = None
        print(f"[{ts}]   iat=absent (allowed — skip expiry check), jti={jti or '-'}")

    sig_hex = sig.hex()
    print(f"[{ts}]   Signing input length: {len(si)} bytes")
    print(f"[{ts}]   Signature ({len(sig)} bytes): {sig_hex[:48]}{'...' if len(sig_hex) > 48 else ''}")
    print(f"[{ts}]   Rule 1 MALFORMED: PASS (valid 3-segment JWS, JSON objects)")

    if alg is not None and alg.lower() == "none":
        print(f"[{ts}]   Rule 2 ALG_NONE: MATCH (alg=\"{alg}\", case-insensitive none)")
        print(f"[{ts}]   => verdict=invalid, reason=ALG_NONE, kid={kid}, alg={alg}")
        results[tid] = ("invalid", "ALG_NONE", kid, alg)
        print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
        print()
        continue
    print(f"[{ts}]   Rule 2 ALG_NONE: PASS (alg={alg})")

    if alg not in policy["allowed_algs"]:
        print(f"[{ts}]   Rule 3 UNSUPPORTED_ALG: MATCH ({alg} not in {policy['allowed_algs']})")
        print(f"[{ts}]   => verdict=invalid, reason=UNSUPPORTED_ALG, kid={kid}, alg={alg}")
        results[tid] = ("invalid", "UNSUPPORTED_ALG", kid, alg)
        print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
        print()
        continue
    print(f"[{ts}]   Rule 3 UNSUPPORTED_ALG: PASS ({alg} in allowed_algs)")

    if policy["require_kid"] and "kid" not in h:
        print(f"[{ts}]   Rule 4 MISSING_KID: MATCH (require_kid=true, header has no kid)")
        print(f"[{ts}]   => verdict=invalid, reason=MISSING_KID, kid={kid}, alg={alg}")
        results[tid] = ("invalid", "MISSING_KID", kid, alg)
        print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
        print()
        continue
    print(f"[{ts}]   Rule 4 MISSING_KID: PASS (kid={kid} present)")

    if kid not in keys:
        print(f"[{ts}]   Rule 5 UNKNOWN_KID: MATCH (kid={kid} not in key ring)")
        print(f"[{ts}]   => verdict=invalid, reason=UNKNOWN_KID, kid={kid}, alg={alg}")
        results[tid] = ("invalid", "UNKNOWN_KID", kid, alg)
        print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
        print()
        continue
    print(f"[{ts}]   Rule 5 UNKNOWN_KID: PASS (kid={kid} found in key ring)")

    key = keys[kid]
    if alg != key["alg"]:
        print(f"[{ts}]   Rule 6 ALG_MISMATCH: MATCH (header alg={alg} != key alg={key['alg']})")
        print(f"[{ts}]   => verdict=invalid, reason=ALG_MISMATCH, kid={kid}, alg={alg}")
        results[tid] = ("invalid", "ALG_MISMATCH", kid, alg)
        print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
        print()
        continue
    print(f"[{ts}]   Rule 6 ALG_MISMATCH: PASS ({alg} matches key)")

    if iat is not None:
        delta = abs(policy["as_of"] - iat)
        if delta > policy["tolerance_seconds"]:
            print(f"[{ts}]   Rule 7 EXPIRED: MATCH (|{policy['as_of']}-{iat}|={delta} > {policy['tolerance_seconds']})")
            print(f"[{ts}]   => verdict=invalid, reason=EXPIRED, kid={kid}, alg={alg}")
            results[tid] = ("invalid", "EXPIRED", kid, alg)
            print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
            print()
            continue
        print(f"[{ts}]   Rule 7 EXPIRED: PASS (delta={delta}s <= {policy['tolerance_seconds']}s)")
    else:
        print(f"[{ts}]   Rule 7 EXPIRED: SKIP (no iat in payload)")

    exp_sig = hmac.new(key["secret"], si, DIGESTS[alg]).digest()
    if not hmac.compare_digest(exp_sig, sig):
        exp_hex = exp_sig.hex()
        print(f"[{ts}]   Rule 8 BAD_SIGNATURE: MATCH")
        print(f"[{ts}]     Expected HMAC: {exp_hex[:48]}...")
        print(f"[{ts}]     Actual sig:    {sig_hex[:48]}...")
        print(f"[{ts}]   => verdict=invalid, reason=BAD_SIGNATURE, kid={kid}, alg={alg}")
        results[tid] = ("invalid", "BAD_SIGNATURE", kid, alg)
        print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
        print()
        continue
    print(f"[{ts}]   Rule 8 BAD_SIGNATURE: PASS (HMAC-{alg.replace('HS','')} verified)")

    reason = "OK"
    if jti is not None and jti in consumed:
        reason = "REPLAY"
        print(f"[{ts}]   Rule 9 REPLAY: MATCH (jti={jti} already consumed)")
    elif jti is not None:
        consumed.add(jti)
        print(f"[{ts}]   Rule 9 REPLAY: PASS (jti={jti}, first seen — now consumed)")
    else:
        print(f"[{ts}]   Rule 9 REPLAY: SKIP (no jti field)")

    verdict = "valid" if reason == "OK" else "invalid"
    print(f"[{ts}]   => verdict={verdict}, reason={reason}, kid={kid}, alg={alg}")
    results[tid] = (verdict, reason, kid, alg)
    print(f"[{ts}]   Processing time: {0.8 + idx * 0.1:.1f}ms")
    print(f"[{ts}]   Heap: 12.{idx}MB | GC collections: 0")
    print()

# Summary
valid_count = sum(1 for v, r, k, a in results.values() if v == "valid")
invalid_count = len(results) - valid_count
by_reason = {}
for v, r, k, a in results.values():
    by_reason[r] = by_reason.get(r, 0) + 1

print("--- Summary ---")
print(f"Total tokens processed: {len(results)}")
print(f"Valid: {valid_count}")
print(f"Invalid: {invalid_count}")
print()
print("By reason:")
for r in sorted(by_reason.keys()):
    print(f"  {r}: {by_reason[r]}")
print()
print("Token verdicts (sorted by id):")
for tid in sorted(results.keys()):
    v, r, k, a = results[tid]
    print(f"  {tid}: verdict={v}, reason={r}, kid={k}, alg={a}")
print()
print("Run complete. Total elapsed: 0.234s")
print("Memory peak: 14.2 MB")
print("Engine: jws-audit-v2.3.1 — no errors during processing")
