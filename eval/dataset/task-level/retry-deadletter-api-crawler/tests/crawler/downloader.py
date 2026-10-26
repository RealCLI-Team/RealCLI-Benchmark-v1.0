from __future__ import annotations

from collections import Counter, defaultdict, deque

from crawler.models import Request, Response


class SequenceDownloader:
    def __init__(self, responses: dict[str, list[Response]]) -> None:
        self._responses = {url: deque(items) for url, items in responses.items()}
        self.fetch_counts: Counter[str] = Counter()

    def fetch(self, request: Request) -> Response:
        self.fetch_counts[request.url] += 1
        queue = self._responses.get(request.url)
        if not queue:
            return Response(url=request.url, status=404, body="")
        if len(queue) == 1:
            return queue[0]
        return queue.popleft()
