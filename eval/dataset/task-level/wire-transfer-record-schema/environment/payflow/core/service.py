from __future__ import annotations

from payflow.core.audit import approvals_for, trace_id_for
from payflow.core.ledger import build_entries
from payflow.core.risk import classify_transfer
from payflow.domain.models import TransferCommand
from payflow.persistence.repository import MemoryRecordStore
from payflow.persistence.writers import build_record


class TransferService:
    def __init__(self, store: MemoryRecordStore) -> None:
        self.store = store

    def accept(self, command: TransferCommand) -> dict:
        risk = classify_transfer(command)
        entries = build_entries(command)
        approvals = approvals_for(command, risk)
        record = build_record(command, entries, risk, approvals, trace_id_for(command))
        return self.store.write(record)
