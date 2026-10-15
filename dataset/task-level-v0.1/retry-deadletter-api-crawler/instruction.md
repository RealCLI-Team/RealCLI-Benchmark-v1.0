This crawler-development instance is inspired by production API crawlers that
use request queues, retry middleware, and failed-request stores similar to
Crawlee/Scrapy-style systems.

Our partner inventory API is unstable. It sometimes returns 429 with a
Retry-After header, sometimes returns transient 5xx for detail endpoints, and
occasionally returns a pagination cursor that points back to a page already
seen. The current crawler silently drops non-200 responses, so the downstream
warehouse misses records and operators have no dead-letter trail.

Please make the API crawler resilient.

Business requirements:

- Retry HTTP 429 and 5xx responses up to the configured retry budget.
- Honor Retry-After seconds when present; otherwise use the configured backoff
  policy.
- Do not retry non-retryable 4xx responses. They must be written to the
  dead-letter output with enough context for operators. Write dead-letter
  entries through `JsonlPipeline.write_deadletter(request, response, reason)`
  and use `reason = "http_<status>"` (for example `"http_404"`) for
  non-retryable responses; when a request exhausts its retry budget, use
  `reason = "retry_exhausted_http_<status>"` instead, so operators can group
  failures by cause.
- `ApiCrawler.run()` must return a stats dict that includes the keys
  `"fetched"`, `"emitted"`, and `"deadletters"` (the number of requests
  written to the dead-letter output).
- The request queue must avoid pagination loops and duplicate detail requests.
- Detail records must be emitted exactly once even if multiple list pages point
  at the same detail URL.
- Existing successful list/detail crawl behavior and JSONL output format must
  continue to work.
- The implementation should use the existing downloader, scheduler, parser,
  pipeline, and clock abstractions instead of sleeping for real or hard-coding
  fixture answers.

The tests run against deterministic in-memory API fixtures; do not add live
network access.
