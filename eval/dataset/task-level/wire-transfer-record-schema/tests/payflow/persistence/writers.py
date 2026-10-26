from __future__ import annotations

from decimal import Decimal

from payflow.domain.models import Approval, LedgerEntry, TransferCommand
from payflow.settings.regions import REGION_BY_CURRENCY


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def entry_to_json(entry: LedgerEntry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "account": entry.account,
        "direction": entry.direction,
        "amount": money(entry.amount),
        "currency": entry.currency,
        "memo": entry.memo,
    }


def approval_to_json(approval: Approval) -> dict:
    return {
        "approver": approval.approver,
        "role": approval.role,
        "approved_at": approval.approved_at,
    }


def build_record(command: TransferCommand, entries: list[LedgerEntry], risk: dict, approvals: list[Approval], trace_id: str) -> dict:
    return {
        "record_type": "wire_transfer_record",
        "schema_version": 3,
        "transfer": {
            "id": command.transfer_id,
            "external_reference": command.external_reference,
            "amount": money(command.amount),
            "currency": command.currency,
            "source_account": {
                "id": command.source.account_id,
                "type": command.source.account_type,
                "country": command.source.country,
            },
            "destination_account": {
                "id": command.destination.account_id,
                "type": command.destination.account_type,
                "country": command.destination.country,
            },
            "counterparty": {
                "name": command.counterparty.name,
                "country": command.counterparty.country,
                "bank_code": command.counterparty.bank_code,
            },
            "requested_at": command.requested_at,
            "status": "accepted",
        },
        "ledger": {
            "entries": [entry_to_json(entry) for entry in entries],
            "balanced": True,
        },
        "risk": risk,
        "audit": {
            "actor": command.actor,
            "channel": command.channel,
            "approvals": [approval_to_json(approval) for approval in approvals],
            "trace_id": trace_id,
        },
        "metadata": {
            "source_event": "wire.transfer.accepted",
            "idempotency_key": command.idempotency_key,
            "region": REGION_BY_CURRENCY.get(command.currency, "global-ledger"),
            "tags": list(command.tags),
        },
    }
