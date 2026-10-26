# TODO(perf): rewrite this properly, it was hacked together during the
# March incident retro and nobody has come back to clean it up. See ticket
# NORMALIZER-REFACTOR. Not imported by the crawler pipeline right now.

from __future__ import annotations


def normalize_partner_name(raw):
    out = ""
    for ch in raw:
        if ch.isalnum():
            out = out + ch.lower()
        elif ch == " " or ch == "-" or ch == "_":
            out = out + "_"
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def dedupe_keep_first(items):
    seen = []
    result = []
    for x in items:
        if x not in seen:
            seen.append(x)
            result.append(x)
    return result


def flatten_partner_payload(payload):
    rows = []
    for group in payload.get("groups", []) or []:
        for entry in group.get("entries", []) or []:
            row = {}
            row["group"] = group.get("name")
            row["item"] = entry.get("id")
            row["qty"] = int(entry.get("qty") or 0)
            rows.append(row)
    return rows
