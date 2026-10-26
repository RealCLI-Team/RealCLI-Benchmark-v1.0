from __future__ import annotations

from payflow.domain.errors import DuplicateTransferError


class IdempotencyLedger:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def claim(self, key: str) -> None:
        if key in self._seen:
            raise DuplicateTransferError(key)
        self._seen.add(key)
