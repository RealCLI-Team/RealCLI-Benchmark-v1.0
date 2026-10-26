from __future__ import annotations


class Outbox:
    def __init__(self) -> None:
        self.pending: list[dict] = []

    def enqueue_record_written(self, record: dict) -> None:
        self.pending.append({"event": "record.written", "record_id": record["transfer"]["id"]})
