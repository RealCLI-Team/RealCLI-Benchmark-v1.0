from __future__ import annotations

from crawler.config import ApiCrawlerConfig
from crawler.downloader import SequenceDownloader
from crawler.models import Request
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
    ) -> None:
        self.config = config
        self.downloader = downloader
        self.parser = parser
        self.pipeline = pipeline

    def run(self) -> dict[str, int]:
        queue = RequestQueue()
        queue.enqueue(Request(url=self.config.start_url, kind="list"))
        fetched = 0
        emitted = 0
        while True:
            request = queue.next_request()
            if request is None:
                break
            response = self.downloader.fetch(request)
            fetched += 1
            parsed = self.parser.parse(request, response)
            if parsed.record is not None:
                self.pipeline.write_record(parsed.record)
                emitted += 1
            for child in parsed.requests:
                queue.enqueue(child)
        return {"fetched": fetched, "emitted": emitted}
