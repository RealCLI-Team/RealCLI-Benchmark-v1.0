from __future__ import annotations


class MemoryRecordStore:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def write(self, record: dict) -> dict:
        self.records.append(record)
        return record
