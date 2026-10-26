# Compact JWS Token Reverse-Audit and Replay Engine

An audit team archived a week of inbound signed webhooks as **compact JWS strings**
(HMAC family). You must reverse-decode each token, verify it against a key set and a
policy, detect algorithm downgrade and replay, and emit a deterministic audit report.

## Input package (`/app/inputs`)

- `tokens.jsonl` — one JSON record per line:
  `{"id": str, "token": str, "received_at": int}`.
  `token` is a compact JWS of the form
  `base64url(header).base64url(payload).base64url(signature)` with **no `=` padding**.
  `header` decodes to JSON such as `{"alg":"HS256","kid":"k1"}`. `payload` decodes to
  JSON that MAY contain `"jti"` (string) and `"iat"` (integer unix seconds).
- `keys.json` — `{"keys": [{"kid": str, "alg": "HS256"|"HS512", "secret_b64url": str}]}`.
  `secret_b64url` decodes (base64url) to the raw HMAC key bytes.
- `policy.json` — `{"allowed_algs": ["HS256","HS512"], "require_kid": bool,
  "tolerance_seconds": int, "as_of": int}`.

## Task

Write a Python program at `/app/solution.py` with this command-line interface:

```
python3 /app/solution.py --input /app/inputs --output /app/result.json
```

For each token, assign **exactly one** `verdict` (`"valid"` or `"invalid"`) and **one**
`reason`, chosen by the following **fixed precedence** (the first matching rule wins):

1. `MALFORMED` — the token is not exactly 3 non-empty base64url parts, or the header or
   payload is not valid JSON after decoding (or does not decode to a JSON object), or
   `iat` is present in the payload but is not an integer.
2. `ALG_NONE` — the header `alg` equals `"none"` (case-insensitive).
3. `UNSUPPORTED_ALG` — `alg` is not in `policy.allowed_algs`.
4. `MISSING_KID` — `require_kid` is true and the header has no `kid`.
5. `UNKNOWN_KID` — the `kid` is not found in `keys.json`.
6. `ALG_MISMATCH` — the header `alg` differs from the matched key's `alg`.
7. `EXPIRED` — the payload has an integer `iat` and
   `abs(as_of - iat) > tolerance_seconds`. (If `iat` is absent, skip this check — that is
   allowed and is NOT a failure.)
8. `BAD_SIGNATURE` — the recomputed `HMAC(key, "header.payload")` (computed over the raw
   base64url ASCII of the first two segments, joined by a single `.`) does not equal the
   token's signature bytes. Use a constant-time comparison.
9. `REPLAY` — the token passed checks 1–8 but its `jti` was already consumed by an
   earlier successfully-verified token. Tokens without a `jti` are never `REPLAY`.
10. `OK` — otherwise. This is the only `valid` verdict.

### Replay ordering

Process tokens sorted by `(received_at, id)` ascending. A `jti` is "consumed" only by the
first token that reaches step 9 with reason `OK`. Any later `OK`-eligible token carrying
the same `jti` becomes `invalid` with reason `REPLAY` and does NOT re-consume the `jti`.

Note that the **processing order** (by `received_at`, then `id`) is independent of the
**output order** (see below).

## Output

Write the report to the `--output` path as JSON:

```json
{
  "summary": {
    "total": N,
    "valid": V,
    "invalid": I,
    "by_reason": {"OK": ..., "MALFORMED": ..., "...": ...}
  },
  "results": [
    {"id": ..., "verdict": ..., "reason": ..., "kid": <str|null>, "alg": <str|null>}
  ]
}
```

- `by_reason` includes every reason whose count is at least 1 (omit reasons with count 0).
- `results` is sorted by `id` ascending (output order is independent of processing order).
- `kid` and `alg` are the decoded header values (strings), or `null` if they are not
  present or the token could not be decoded.
- Serialize with `json.dumps(..., sort_keys=True, indent=2)` and end the file with a
  single trailing newline. Reruns must produce byte-identical output.
