# Add a "conditional request" incremental crawling middleware to Scrapy

We maintain a copy of the Scrapy source code in `/app`, and in production we run a large fleet of periodically scheduled spiders (one round daily or hourly, with largely unchanged start URLs). The problem now is: the vast majority of pages haven't changed at all between two rounds, yet every round they get fully downloaded and fully parsed again, wasting a lot of bandwidth and CPU. The target sites all properly return `ETag` and `Last-Modified`, and they also respond correctly to conditional requests with `304 Not Modified`, so I want incremental re-crawling handled at the framework level rather than having every business spider deal with it on its own.

For the record: I've looked at the existing `HttpCacheMiddleware`. It writes entire response bodies to disk — far too much storage — and on a hit it serves the stale response directly, which doesn't match our requirement of "if the page hasn't changed, treat it as if it doesn't exist; if it has, parse it normally as a new page". What we need this time is a lightweight solution that **only records validators and never stores response bodies**.

## Required behavior

Implement a new built-in downloader middleware in the code at `/app`, with the following external contract (please follow it strictly — our downstream ops scripts and monitoring integrate against this contract):

1. **How it is enabled**: the middleware class lives at `scrapy.downloadermiddlewares.conditional.ConditionalRequestsMiddleware`, is registered into the default downloader middleware chain like the framework's other built-in middlewares, but takes no effect by default; it is only actually enabled when the user sets `CONDITIONAL_REQUESTS_ENABLED` to `True` (the enable/disable check should follow the same convention as the other optional middlewares in the framework).

2. **Recording validators**: for `200` responses to `GET`/`HEAD` requests, if they carry an `ETag` and/or `Last-Modified` header, record those values keyed by request fingerprint, with later values for the same resource overwriting earlier ones. Do not record when the response's `Cache-Control` contains `no-store`. All other status codes (except the 304 in item 4 below) are never recorded and pass through unchanged.

3. **Issuing conditional requests**: afterwards, for `GET`/`HEAD` requests to the same resource (same request fingerprint), automatically add `If-None-Match` and/or `If-Modified-Since` headers before downloading, with values exactly identical to the ones originally received (weak validators such as `W/"..."` are echoed back verbatim). If the request already carries a user-set header of the same name, **never overwrite it**: headers the user has set stay as-is, and only headers the user hasn't set are filled in from the stored validators. For requests whose conditional headers were all set by the user, their 304s are none of our business and are passed through to the spider unchanged.

4. **Handling 304**: whenever a request had conditional headers added by this middleware and the server replies with `304 Not Modified`, the response must be dropped before it reaches the spider — it must not enter the `parse` callback, must not produce items, must not trigger follow-up requests; behaviorally it is equivalent to the request being normally ignored by the framework.

5. **Per-request opt-out**: when a spider sets `request.meta["conditional_request"] = False`, that request is completely unaffected by any of the logic above (no conditional headers added, 304 passed through, response validators not recorded).

6. **Persistence across rounds**: by default validators live only in memory (they expire when the process ends); once the user sets `CONDITIONAL_REQUESTS_DIR` to a directory, validators must be persisted per spider under that directory (create the directory automatically if it doesn't exist), so that a new process started the next day can send conditional requests right away. Implement the persistence backend using only the Python standard library — do not introduce any new third-party dependency; if individual stored entries are corrupted, the spider must not crash — just treat them as if nothing was stored.

7. **Stats instrumentation**: maintain three counters in Scrapy's stats, with these fixed names:
   - `conditional_requests/conditional`: number of requests to which this middleware added conditional headers
   - `conditional_requests/store`: number of responses whose validators were recorded
   - `conditional_requests/not_modified`: number of 304 responses that were dropped

8. As part of the framework, also update the user documentation accordingly (the downloader middleware docs, the descriptions of the related settings, and the description of the special `Request.meta` keys), and add automated tests covering the behaviors above, in a style consistent with the repository's existing middleware tests.

## The final effect I expect

Same spider, same set of start URLs, with `CONDITIONAL_REQUESTS_ENABLED=True` and `CONDITIONAL_REQUESTS_DIR` configured, run twice back to back: the first round behaves exactly as before (items are produced normally, and the server only receives plain requests without conditional headers); on the second round every request the server receives carries `If-None-Match`, unchanged pages return 304 and are dropped directly by the framework, the spider produces no items at all, and the stats show the corresponding numbers for the three counters above.

While implementing, carefully read through the existing downloader middlewares (especially `HttpCacheMiddleware`), the downloader middleware manager, and the request-fingerprint-related utility code; write everything following the repository's established conventions, and don't patch anything at the spider layer.
