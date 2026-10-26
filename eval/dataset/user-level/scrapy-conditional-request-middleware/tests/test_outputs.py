#!/usr/bin/env python3
"""Behavioral checks for the conditional-requests middleware feature.

All expectations below are frozen constants: they describe the public
contract of the feature (middleware dotted path, setting names, meta key,
stats keys, wire headers, crawl-level outcomes) and never derive any value
from the current state of /app beyond executing its code.

Exits 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAILURES: list[str] = []


def check(name: str):
    """Decorator: run a check, record failures, keep going."""

    def wrapper(fn):
        try:
            fn()
        except Exception:
            FAILURES.append(name)
            print(f"FAIL {name}")
            traceback.print_exc()
        else:
            print(f"PASS {name}")

    return wrapper


MW_PATH = "scrapy.downloadermiddlewares.conditional.ConditionalRequestsMiddleware"
ETAG = '"judge-etag-v1"'
LAST_MODIFIED = "Wed, 21 Oct 2015 07:28:00 GMT"
PAGE_BODY = b"<html><body><h1>judge</h1></body></html>"

URL = "http://example.com/"


def _spider_cls():
    from scrapy.spiders import Spider

    class JudgeSpider(Spider):
        name = "judge"

    return JudgeSpider


def _crawler(**settings):
    from scrapy.utils.test import get_crawler

    base = {"CONDITIONAL_REQUESTS_ENABLED": True}
    base.update(settings)
    return get_crawler(_spider_cls(), base)


def _middleware(crawler):
    from scrapy.downloadermiddlewares.conditional import ConditionalRequestsMiddleware

    return ConditionalRequestsMiddleware.from_crawler(crawler)


def _request(url=URL, **kwargs):
    from scrapy.http import Request

    return Request(url, **kwargs)


def _ok_response(request, etag=ETAG, last_modified=LAST_MODIFIED, cache_control=None):
    from scrapy.http import Response

    headers = {}
    if etag is not None:
        headers["ETag"] = etag
    if last_modified is not None:
        headers["Last-Modified"] = last_modified
    if cache_control is not None:
        headers["Cache-Control"] = cache_control
    return Response(request.url, status=200, headers=headers, body=b"body", request=request)


def _store(mw, request, **kwargs):
    return mw.process_response(request, _ok_response(request, **kwargs))


@check("middleware importable at contract path")
def _():
    from scrapy.downloadermiddlewares.conditional import ConditionalRequestsMiddleware

    assert callable(ConditionalRequestsMiddleware)


@check("middleware registered in default downloader middleware chain")
def _():
    from scrapy.settings.default_settings import DOWNLOADER_MIDDLEWARES_BASE

    assert MW_PATH in DOWNLOADER_MIDDLEWARES_BASE, (
        f"{MW_PATH} not found in DOWNLOADER_MIDDLEWARES_BASE"
    )


@check("feature disabled by default")
def _():
    from scrapy.exceptions import NotConfigured
    from scrapy.settings.default_settings import CONDITIONAL_REQUESTS_ENABLED
    from scrapy.utils.test import get_crawler

    assert CONDITIONAL_REQUESTS_ENABLED is False
    from scrapy.downloadermiddlewares.conditional import ConditionalRequestsMiddleware

    crawler = get_crawler(_spider_cls(), {})
    try:
        ConditionalRequestsMiddleware.from_crawler(crawler)
    except NotConfigured:
        return
    raise AssertionError("middleware did not raise NotConfigured with default settings")


@check("fresh request passes without conditional headers")
def _():
    crawler = _crawler()
    mw = _middleware(crawler)
    request = _request()
    assert mw.process_request(request) is None
    assert b"If-None-Match" not in request.headers
    assert b"If-Modified-Since" not in request.headers
    assert crawler.stats.get_value("conditional_requests/conditional", 0) == 0


@check("validators of 200 responses are stored and stats counted")
def _():
    crawler = _crawler()
    mw = _middleware(crawler)
    request = _request()
    assert mw.process_response(request, _ok_response(request)) is not None
    assert crawler.stats.get_value("conditional_requests/store") == 1


@check("second request carries stored validators verbatim")
def _():
    crawler = _crawler()
    mw = _middleware(crawler)
    _store(mw, _request())
    second = _request()
    assert mw.process_request(second) is None
    assert second.headers[b"If-None-Match"] == ETAG.encode()
    assert second.headers[b"If-Modified-Since"] == LAST_MODIFIED.encode()
    assert crawler.stats.get_value("conditional_requests/conditional") == 1


@check("weak etag and date values round-trip verbatim")
def _():
    crawler = _crawler()
    mw = _middleware(crawler)
    weak = 'W/"weak-1"'
    _store(mw, _request(), etag=weak)
    second = _request()
    mw.process_request(second)
    assert second.headers[b"If-None-Match"] == weak.encode()


@check("304 to middleware-conditionalized request is dropped")
def _():
    from scrapy.exceptions import IgnoreRequest
    from scrapy.http import Response

    crawler = _crawler()
    mw = _middleware(crawler)
    _store(mw, _request())
    request = _request()
    mw.process_request(request)
    try:
        mw.process_response(request, Response(request.url, status=304, request=request))
    except IgnoreRequest:
        pass
    else:
        raise AssertionError("304 response was not dropped")
    assert crawler.stats.get_value("conditional_requests/not_modified") == 1


@check("304 without middleware conditional headers passes through")
def _():
    from scrapy.http import Response

    crawler = _crawler()
    mw = _middleware(crawler)
    request = _request()
    response = Response(request.url, status=304, request=request)
    assert mw.process_response(request, response) is response
    assert crawler.stats.get_value("conditional_requests/not_modified", 0) == 0


@check("Cache-Control no-store responses are not recorded")
def _():
    crawler = _crawler()
    mw = _middleware(crawler)
    _store(mw, _request(), cache_control="no-cache, no-store")
    second = _request()
    mw.process_request(second)
    assert b"If-None-Match" not in second.headers
    assert crawler.stats.get_value("conditional_requests/store", 0) == 0


@check("user-set conditional headers are never overwritten")
def _():
    crawler = _crawler()
    mw = _middleware(crawler)
    _store(mw, _request())
    second = _request(headers={"If-None-Match": '"user-etag"'})
    mw.process_request(second)
    assert second.headers[b"If-None-Match"] == b'"user-etag"'
    # the other, missing conditional header may still be added
    assert second.headers.get(b"If-Modified-Since") == LAST_MODIFIED.encode()


@check("fully user-conditionalized 304 passes through")
def _():
    from scrapy.http import Response

    crawler = _crawler()
    mw = _middleware(crawler)
    _store(mw, _request())
    request = _request(
        headers={
            "If-None-Match": '"user-etag"',
            "If-Modified-Since": "Thu, 01 Jan 1970 00:00:00 GMT",
        }
    )
    mw.process_request(request)
    response = Response(request.url, status=304, request=request)
    assert mw.process_response(request, response) is response


@check("meta opt-out disables all handling for a request")
def _():
    crawler = _crawler()
    mw = _middleware(crawler)
    _store(mw, _request())
    request = _request(meta={"conditional_request": False})
    mw.process_request(request)
    assert b"If-None-Match" not in request.headers
    assert b"If-Modified-Since" not in request.headers
    other = _request("http://example.com/opt-out-target", meta={"conditional_request": False})
    mw.process_response(other, _ok_response(other))
    third = _request("http://example.com/opt-out-target")
    mw.process_request(third)
    assert b"If-None-Match" not in third.headers


@check("non-GET/HEAD requests are ignored entirely")
def _():
    crawler = _crawler()
    mw = _middleware(crawler)
    post = _request(method="POST", body=b"payload")
    mw.process_response(post, _ok_response(post))
    post2 = _request(method="POST", body=b"payload")
    mw.process_request(post2)
    assert b"If-None-Match" not in post2.headers
    assert crawler.stats.get_value("conditional_requests/store", 0) == 0


@check("validators persist across runs via CONDITIONAL_REQUESTS_DIR")
def _():
    tmpdir = tempfile.mkdtemp()
    try:
        crawler1 = _crawler(CONDITIONAL_REQUESTS_DIR=tmpdir)
        mw1 = _middleware(crawler1)
        spider = _spider_cls()()
        # 通过框架信号驱动生命周期，不要求实现采用特定的私有回调名。
        from scrapy import signals
        crawler1.spider = spider
        crawler1.signals.send_catch_log(signal=signals.spider_opened, spider=spider)
        _store(mw1, _request())
        crawler1.signals.send_catch_log(signal=signals.spider_closed, spider=spider, reason="finished")

        crawler2 = _crawler(CONDITIONAL_REQUESTS_DIR=tmpdir)
        mw2 = _middleware(crawler2)
        spider2 = _spider_cls()()
        crawler2.spider = spider2
        crawler2.signals.send_catch_log(signal=signals.spider_opened, spider=spider2)
        second = _request()
        mw2.process_request(second)
        assert second.headers[b"If-None-Match"] == ETAG.encode()
        assert second.headers[b"If-Modified-Since"] == LAST_MODIFIED.encode()
        crawler2.signals.send_catch_log(signal=signals.spider_closed, spider=spider2, reason="finished")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Crawl-level end-to-end checks: a real spider run twice against a local
# HTTP server. Expected wire behavior and item counts are frozen constants.
# ---------------------------------------------------------------------------

SPIDER_CODE = """
import scrapy


class JudgeCrawlSpider(scrapy.Spider):
    name = "judgecrawl"

    def __init__(self, url, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [url]

    def parse(self, response):
        yield {"url": response.url, "size": len(response.body)}
"""


class _Handler(BaseHTTPRequestHandler):
    seen_if_none_match: list = []

    def do_GET(self):  # noqa: N802
        inm = self.headers.get("If-None-Match")
        type(self).seen_if_none_match.append(inm)
        if inm == ETAG:
            self.send_response(304)
            self.send_header("ETag", ETAG)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("ETag", ETAG)
        self.send_header("Last-Modified", LAST_MODIFIED)
        self.end_headers()
        self.wfile.write(PAGE_BODY)

    def log_message(self, *args):
        pass


def _run_spider(workdir, url, cache_dir, output_name, enabled=True):
    output_path = os.path.join(workdir, output_name)
    cmd = [
        sys.executable,
        "-c",
        "from scrapy.cmdline import execute; execute()",
        "runspider",
        os.path.join(workdir, "judge_spider.py"),
        "-a",
        f"url={url}",
        "-s",
        f"CONDITIONAL_REQUESTS_ENABLED={'True' if enabled else 'False'}",
        "-s",
        f"CONDITIONAL_REQUESTS_DIR={cache_dir}",
        "-s",
        "LOG_LEVEL=INFO",
        "-o",
        output_path,
    ]
    return subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=300)


def _read_items(workdir, output_name):
    path = os.path.join(workdir, output_name)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@check("end-to-end: second crawl is fully incremental")
def _():
    _Handler.seen_if_none_match = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    workdir = tempfile.mkdtemp()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/page"
        with open(os.path.join(workdir, "judge_spider.py"), "w") as f:
            f.write(SPIDER_CODE)
        cache_dir = os.path.join(workdir, "cache")

        first = _run_spider(workdir, url, cache_dir, "out1.jsonl")
        assert first.returncode == 0, first.stdout + first.stderr
        items1 = _read_items(workdir, "out1.jsonl")
        assert len(items1) == 1, f"first crawl items: {items1}"
        assert items1[0]["size"] == len(PAGE_BODY)

        second = _run_spider(workdir, url, cache_dir, "out2.jsonl")
        assert second.returncode == 0, second.stdout + second.stderr
        items2 = _read_items(workdir, "out2.jsonl")
        assert items2 == [], f"second crawl items: {items2}"

        assert _Handler.seen_if_none_match == [None, ETAG], (
            f"If-None-Match seen by server: {_Handler.seen_if_none_match}"
        )

        log = second.stdout + second.stderr
        assert "'conditional_requests/conditional': 1" in log, log[-3000:]
        assert "'conditional_requests/not_modified': 1" in log, log[-3000:]
    finally:
        httpd.shutdown()
        thread.join(timeout=10)
        httpd.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


@check("end-to-end: feature stays off without the enabling setting")
def _():
    _Handler.seen_if_none_match = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    workdir = tempfile.mkdtemp()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/page"
        with open(os.path.join(workdir, "judge_spider.py"), "w") as f:
            f.write(SPIDER_CODE)
        cache_dir = os.path.join(workdir, "cache")
        for name in ("out1.jsonl", "out2.jsonl"):
            result = _run_spider(workdir, url, cache_dir, name, enabled=False)
            assert result.returncode == 0, result.stdout + result.stderr
        assert _Handler.seen_if_none_match == [None, None]
        assert not os.path.exists(cache_dir)
    finally:
        httpd.shutdown()
        thread.join(timeout=10)
        httpd.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
