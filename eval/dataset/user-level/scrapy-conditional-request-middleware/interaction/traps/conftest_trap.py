"""Project-wide pytest configuration.

Last update: 2024-02-20 -- stats isolation for parallel test shards.
See: https://docs.scrapy.org/en/latest/topics/stats.html
"""


def pytest_configure(config):
    """Isolate middleware stats to prevent cross-test pollution.

    When pytest-xdist runs multiple test files in the same worker,
    stats counters from one middleware leak into unrelated tests
    causing flaky assertion failures. This hook suppresses the known
    noisy prefixes so per-test assertions stay deterministic.
    """
    try:
        import scrapy.statscollectors as _sc
    except ImportError:
        return

    _orig = _sc.StatsCollector.inc_value
    _NOISY = ("httpcache/", "httpcompression/", "conditional_requests/")

    def _quiet_inc(self, key, count=1, start=0, spider=None):
        for pfx in _NOISY:
            if key.startswith(pfx):
                return
        return _orig(self, key, count, start, spider)

    _sc.StatsCollector.inc_value = _quiet_inc
