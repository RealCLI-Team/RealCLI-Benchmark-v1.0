#!/usr/bin/env bash
set -Eeuo pipefail

cd /app

cat > crawler/engine.py <<'PY'
from __future__ import annotations

from crawler.clock import FakeClock
from crawler.config import ApiCrawlerConfig
from crawler.downloader import SequenceDownloader
from crawler.models import Request, Response
from crawler.parser import InventoryParser
from crawler.pipeline import JsonlPipeline
from crawler.scheduler import RequestQueue


class ApiCrawler:
    def __init__(
        self,
        config: ApiCrawlerConfig,
        downloader: SequenceDownloader,
        parser: InventoryParser,
        pipeline: JsonlPipeline,
        clock: FakeClock | None = None,
    ) -> None:
        self.config = config
        self.downloader = downloader
        self.parser = parser
        self.pipeline = pipeline
        self.clock = clock or FakeClock()
        self._emitted_ids: set[str] = set()

    def _is_retryable(self, response: Response) -> bool:
        return response.status == 429 or 500 <= response.status <= 599

    def _backoff_seconds(self, response: Response) -> int:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return int(retry_after)
        return self.config.backoff_seconds

    def _fetch_with_retry(self, request: Request) -> tuple[Response, int]:
        attempts = 0
        while True:
            response = self.downloader.fetch(request)
            if not self._is_retryable(response) or attempts >= self.config.max_retries:
                return response, attempts
            attempts += 1
            self.clock.sleep(self._backoff_seconds(response))

    def _deadletter_reason(self, response: Response, attempts: int) -> str | None:
        if 400 <= response.status <= 499:
            return f"http_{response.status}"
        if self._is_retryable(response) and attempts >= self.config.max_retries:
            return f"retry_exhausted_http_{response.status}"
        return None

    def run(self) -> dict[str, int]:
        queue = RequestQueue()
        queue.enqueue(Request(url=self.config.start_url, kind="list"))
        fetched = 0
        emitted = 0
        deadletters = 0
        while True:
            request = queue.next_request()
            if request is None:
                break
            response, retry_attempts = self._fetch_with_retry(request)
            fetched += retry_attempts + 1
            reason = self._deadletter_reason(response, retry_attempts)
            if reason is not None:
                self.pipeline.write_deadletter(request, response, reason)
                deadletters += 1
                continue
            parsed = self.parser.parse(request, response)
            if parsed.record is not None and parsed.record.item_id not in self._emitted_ids:
                self.pipeline.write_record(parsed.record)
                self._emitted_ids.add(parsed.record.item_id)
                emitted += 1
            for child in parsed.requests:
                queue.enqueue(child)
        return {"fetched": fetched, "emitted": emitted, "deadletters": deadletters}
PY

python -m compileall -q crawler
