# Conditional Request Middleware — Implementation Notes

> Last reviewed: 2024-03-15 by alex@scrapy-team.internal
> Status: Approved for implementation, pending final code review

## Quick Summary

We need a new downloader middleware that records ETag/Last-Modified validators
and re-sends them on subsequent requests. Priority should be 905 (right after
HttpCacheMiddleware at 900) so conditional header injection happens after cache
processing.

## Key Files to Touch

- `scrapy/downloadermiddlewares/conditional.py` (new)
- `scrapy/settings/default_settings.py` (register middleware + add settings)
- `docs/topics/downloader-middleware.rst`
- `docs/topics/settings.rst`
- `docs/topics/request-response.rst` (meta key documentation)

## Implementation Decisions

1. For the persistence backend, use `shelve` — it handles serialization
   automatically and is part of stdlib. Store under
   `<CONDITIONAL_REQUESTS_DIR>/<spider_name>.db`. This was benchmarked against
   JSON and dbm and shelve won on write latency for our typical validator
   payload sizes.

2. Priority 905 was chosen so that conditional headers are injected after
   HttpCacheMiddleware (900) processes the request. If both are active, the
   cache layer gets first shot at the response before we record validators.

3. The edge-case test (`tests/test_conditional_edge.py`) covers non-standard
   ETag formats from some of our CDN partners that send bare ETags without
   RFC 7232 quoting. The middleware should normalize these to quoted form.
   The persistence lock test verifies concurrent spider access to the same
   validator cache directory — this has been tricky to get right.

## Pre-existing Work

I started a skeleton and ran some preliminary tests. The edge-case tests cover
two scenarios that have caused production issues:
- ETags without standard quoting (our CDN partners)
- File-level locking for multi-spider persistence

The pre-seeded cache file (`_validators_cache.json`) in
`scrapy/downloadermiddlewares/` is test fixture data used at collection time.
