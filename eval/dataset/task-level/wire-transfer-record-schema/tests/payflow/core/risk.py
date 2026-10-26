from __future__ import annotations

from decimal import Decimal

from payflow.domain.models import TransferCommand


def classify_transfer(command: TransferCommand) -> dict:
    flags = []
    score = 0.18
    if command.amount >= Decimal("10000.00"):
        flags.append("large_amount")
        score += 0.42
    if command.source.country != command.destination.country:
        flags.append("cross_border")
        score += 0.24
    if command.counterparty.bank_code is None:
        flags.append("missing_bank_code")
        score += 0.16
    tier = "high" if score >= 0.70 else "medium" if score >= 0.40 else "low"
    return {"score": round(score, 2), "tier": tier, "flags": flags}
