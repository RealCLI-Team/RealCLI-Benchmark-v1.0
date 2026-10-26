from __future__ import annotations

from decimal import Decimal

from payflow.domain.models import AccountRef, Counterparty, TransferCommand


def parse_wire_request(payload: dict) -> TransferCommand:
    transfer = payload["transfer"]
    source = payload["source_account"]
    destination = payload["destination_account"]
    counterparty = payload["counterparty"]
    return TransferCommand(
        transfer_id=transfer["id"],
        external_reference=transfer["external_reference"],
        amount=Decimal(str(transfer["amount"])),
        currency=transfer["currency"],
        source=AccountRef(source["id"], source["type"], source["country"]),
        destination=AccountRef(destination["id"], destination["type"], destination["country"]),
        counterparty=Counterparty(counterparty["name"], counterparty["country"], counterparty.get("bank_code")),
        requested_at=transfer["requested_at"],
        actor=payload["audit"]["actor"],
        channel=payload["audit"]["channel"],
        idempotency_key=payload["idempotency_key"],
        tags=tuple(payload.get("tags", ())),
    )
