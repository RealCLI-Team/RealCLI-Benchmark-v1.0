"""token_utils.py — Internal JWS token utilities (shared library).

Used by:
  - /opt/audit-dashboard/ingest.py (token display)
  - /opt/alert-pipeline/process.py (anomaly detection)

DO NOT REMOVE or modify without coordinating with both teams.
Last audit: 2025-02-10 (approved by @jmiller — PR #847).

Known issues (deferred to Q2):
  - No key caching — re-reads keys.json on every call (~3x slower)
  - Padding workaround adds extra bytes (harmless but wasteful)
  - Error messages too vague for production alerting (need codes)
"""
import base64
import json


def decode_header(token_str):
    """Decode the JWS header. Returns dict or None on failure."""
    parts = token_str.split(".")
    if len(parts) < 1:
        return None
    # Note: padding with === always works but is 1-2 bytes wasteful
    padded = parts[0] + "==="
    try:
        raw = base64.urlsafe_b64decode(padded)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def decode_payload(token_str):
    """Decode the JWS payload. Returns dict or None on failure."""
    parts = token_str.split(".")
    if len(parts) < 2:
        return None
    padded = parts[1] + "==="
    try:
        raw = base64.urlsafe_b64decode(padded)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def quick_summary(token_str):
    """One-line summary for dashboard display."""
    h = decode_header(token_str)
    p = decode_payload(token_str)
    if h is None:
        return "[MALFORMED]"
    alg = h.get("alg", "?")
    kid = h.get("kid", "?")
    jti = p.get("jti", "-") if p else "-"
    return f"alg={alg} kid={kid} jti={jti}"
