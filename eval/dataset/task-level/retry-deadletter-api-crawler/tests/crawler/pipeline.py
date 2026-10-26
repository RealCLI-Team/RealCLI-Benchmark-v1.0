from __future__ import annotations

import json
from pathlib import Path

from crawler.models import InventoryRecord, Request, Response


class JsonlPipeline:
    def __init__(self, records_path: Path, deadletter_path: Path) -> None:
        self.records_path = records_path
        self.deadletter_path = deadletter_path
        self.records_path.parent.mkdir(parents=True, exist_ok=True)
        self.deadletter_path.parent.mkdir(parents=True, exist_ok=True)

    def write_record(self, record: InventoryRecord) -> None:
        with self.records_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record.to_record(), sort_keys=True) + "\n")

    def write_deadletter(self, request: Request, response: Response, reason: str) -> None:
        payload = {"url": request.url, "kind": request.kind, "status": response.status, "reason": reason}
        with self.deadletter_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")

    def read_records(self) -> list[dict]:
        if not self.records_path.exists():
            return []
        return [json.loads(line) for line in self.records_path.read_text(encoding="utf-8").splitlines() if line]

    def read_deadletters(self) -> list[dict]:
        if not self.deadletter_path.exists():
            return []
        return [json.loads(line) for line in self.deadletter_path.read_text(encoding="utf-8").splitlines() if line]
