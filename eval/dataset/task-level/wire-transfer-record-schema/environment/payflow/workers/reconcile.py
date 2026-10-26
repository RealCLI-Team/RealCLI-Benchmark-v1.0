from __future__ import annotations


def find_unbalanced(records: list[dict]) -> list[str]:
    return [record["transfer"]["id"] for record in records if not record.get("ledger", {}).get("balanced", False)]
