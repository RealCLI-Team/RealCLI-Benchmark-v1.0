from __future__ import annotations

from payflow.domain.models import LedgerEntry, TransferCommand


def build_entries(command: TransferCommand) -> list[LedgerEntry]:
    debit = LedgerEntry(
        entry_id=f"{command.transfer_id}:D",
        account=command.source.account_id,
        direction="debit",
        amount=command.amount,
        currency=command.currency,
        memo=f"wire transfer {command.external_reference}",
    )
    credit = LedgerEntry(
        entry_id=f"{command.transfer_id}:C",
        account=command.destination.account_id,
        direction="credit",
        amount=command.amount,
        currency=command.currency,
        memo=f"wire transfer {command.external_reference}",
    )
    return [debit, credit]
