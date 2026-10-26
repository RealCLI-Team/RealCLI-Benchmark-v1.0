from __future__ import annotations

from collections import deque

from crawler.models import Request


class RequestQueue:
    def __init__(self) -> None:
        self._queue: deque[Request] = deque()
        self._seen: set[tuple[str, str]] = set()

    def enqueue(self, request: Request) -> bool:
        key = (request.kind, request.url)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._queue.append(request)
        return True

    def next_request(self) -> Request | None:
        if not self._queue:
            return None
        return self._queue.popleft()
