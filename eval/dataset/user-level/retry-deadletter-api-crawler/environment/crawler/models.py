from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Request:
    url: str
    kind: str


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InventoryRecord:
    item_id: str
    name: str
    stock: int

    def to_record(self) -> dict:
        return {"item_id": self.item_id, "name": self.name, "stock": self.stock}
