from __future__ import annotations

import json


def canonical_json(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def copy_for_audit(record: dict) -> dict:
    copied = dict(record)
    copied["audit"] = dict(record["audit"])
    return copied
