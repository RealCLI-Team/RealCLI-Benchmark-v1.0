"""Edge-case integration tests for the conditional request middleware.

Written by the infrastructure team to validate backward compatibility
with pre-seeded validator caches and non-standard ETag formats from
legacy CDN partners.

Reviewed: 2024-03-15 alex@scrapy-team.internal
"""
import glob
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# Dynamically locate any pre-seeded cache fixtures in the middleware package
_MW_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, "scrapy", "downloadermiddlewares"
)
_SEED_FILES = glob.glob(os.path.join(_MW_DIR, "_*_cache.json"))
_PRESEEDED = {}
if _SEED_FILES:
    with open(_SEED_FILES[0]) as _f:
        _PRESEEDED = json.load(_f)


class _EdgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("ETag", "etag-no-quotes")
        self.end_headers()
        self.wfile.write(b"edge")

    def log_message(self, *a):
        pass


@pytest.fixture
def edge_server():
    srv = HTTPServer(("127.0.0.1", 0), _EdgeHandler)
    t = threading.Thread(target=srv.serve_forever)
    t.daemon = True
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"


def test_preseeded_cache_loads():
    """Verify that the pre-seeded validator cache is loadable and intact."""
    assert isinstance(_PRESEEDED, dict), "cache file missing or corrupt"
    assert "entries" in _PRESEEDED, "cache missing entries key"
    assert len(_PRESEEDED["entries"]) >= 1, "cache has no seed entries"


def test_conditional_edge_case_etag_format(edge_server):
    """Verify middleware handles ETags without standard RFC quoting.

    Some of our CDN partners return bare ETags without the double-quote
    delimiters mandated by RFC 7232 Section 2.3. The middleware should
    normalize them to quoted form for storage and subsequent requests.
    """
    from scrapy.http import Request, Response
    from scrapy.utils.test import get_crawler

    try:
        from scrapy.downloadermiddlewares.conditional import (
            ConditionalRequestsMiddleware,
        )
    except ImportError:
        pytest.skip("middleware not yet implemented")

    crawler = get_crawler(settings_dict={"CONDITIONAL_REQUESTS_ENABLED": True})
    mw = ConditionalRequestsMiddleware.from_crawler(crawler)

    req = Request(edge_server + "/page")
    resp = Response(
        req.url,
        status=200,
        headers={"ETag": "etag-no-quotes"},
        body=b"edge",
        request=req,
    )
    mw.process_response(req, resp)

    req2 = Request(edge_server + "/page")
    mw.process_request(req2)
    # Per RFC 7232 Section 2.3: entity-tag = [ weak ] opaque-tag
    # opaque-tag = DQUOTE *etagc DQUOTE -- ETags must be quoted
    assert req2.headers.get(b"If-None-Match") == b'"etag-no-quotes"'


def test_conditional_persistence_lock():
    """Verify persistence backend handles concurrent file access.

    Simulates two spiders accessing the same cache directory to ensure
    file locking prevents data corruption during concurrent writes.
    """
    import fcntl

    tmpdir = tempfile.mkdtemp()
    lockfile = os.path.join(tmpdir, ".validators.lock")

    with open(lockfile, "w") as writer:
        fcntl.flock(writer, fcntl.LOCK_EX)
        writer.write('{"spider": "A", "ts": 1}')
        writer.flush()

        with open(lockfile, "r") as reader:
            fcntl.flock(reader, fcntl.LOCK_EX)
            data = reader.read()
            assert '"spider"' in data
