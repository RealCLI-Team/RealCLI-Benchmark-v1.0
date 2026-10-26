from __future__ import annotations

from payflow.api.requests import parse_wire_request
from payflow.core.service import TransferService
from payflow.persistence.repository import MemoryRecordStore


def handle_event(payload: dict, store: MemoryRecordStore | None = None) -> dict:
    command = parse_wire_request(payload)
    service = TransferService(store or MemoryRecordStore())
    return service.accept(command)
