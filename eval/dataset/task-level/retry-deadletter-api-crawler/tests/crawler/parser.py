from __future__ import annotations

import json
from dataclasses import dataclass

from crawler.models import InventoryRecord, Request, Response


@dataclass(frozen=True)
class ParsedApiPage:
    requests: tuple[Request, ...] = ()
    record: InventoryRecord | None = None


class InventoryParser:
    def parse(self, request: Request, response: Response) -> ParsedApiPage:
        if response.status != 200:
            return ParsedApiPage()
        payload = json.loads(response.body)
        if request.kind == "list":
            requests = [Request(url=item["detail_url"], kind="detail") for item in payload.get("items", [])]
            if payload.get("next"):
                requests.append(Request(url=payload["next"], kind="list"))
            return ParsedApiPage(requests=tuple(requests))
        return ParsedApiPage(
            record=InventoryRecord(
                item_id=str(payload["id"]),
                name=payload["name"],
                stock=int(payload["stock"]),
            )
        )
