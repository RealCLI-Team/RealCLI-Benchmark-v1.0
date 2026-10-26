from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiCrawlerConfig:
    start_url: str
    max_retries: int = 0
    backoff_seconds: int = 1
