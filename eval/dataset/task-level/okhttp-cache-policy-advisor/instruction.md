# OkHttp cache policy advisor

Clone OkHttp and add a feature module that explains whether HTTP responses are cacheable under request/response metadata.


This task is based on a pinned real Java repository snapshot:

- Repository: `https://github.com/square/okhttp.git`
- Ref: `refs/tags/parent-4.12.0`
- Verified ref hash: `f4ff8e57892a044840b1dbb279c840ffdba37b30`

The source archive already contains the cloned snapshot plus a bounded
`terminal-bench-feature` Maven module where the new feature should be
implemented.


The only project input in the environment is:

`/app/input/okhttp-real-repo-src.tar.gz`

The archive contains a top-level `okhttp-real-repo/` directory. Extract it
under `/app` so the project root for this feature is exactly:

`/app/okhttp-real-repo/terminal-bench-feature`

Do not delete or modify `/app/input/okhttp-real-repo-src.tar.gz`.

## Required Java Interface

Implement:

```java
com.acme.challenge.FeatureService#evaluate(String requestJson)
```

Keep the CLI working:

```text
com.acme.challenge.FeatureCli --input <json path> --output <json path>
```

The CLI must read the full input JSON file, invoke `FeatureService`, and write
parseable JSON. The CLI feature-case verifier compares parsed JSON values, but
the bundled public Maven example may compare an exact string. Emit deterministic
JSON with stable object-key ordering and the array ordering required below.

## Feature Requirements

1. Evaluate HTTP method, status code, Cache-Control, Expires, Authorization, and Vary headers.
2. Return cacheable, freshnessSeconds, and ordered reasons.
3. Honor no-store and private directives over freshness directives.
4. Handle missing or malformed date headers without throwing.

## Detailed Behavior Covered By Tests

- Parse request method, response status, and response/request headers case-insensitively. Cacheable methods include `GET` and `HEAD`; other methods are not cacheable and should return reason `method <METHOD> is not cacheable`.
- Honor `Cache-Control` directives. `no-store` makes the response uncacheable, takes precedence over freshness directives, and adds reason `no-store directive`. `private` also prevents shared caching unless another explicit rule says otherwise.
- `max-age=<seconds>` sets `freshnessSeconds` and adds reason `explicit max-age`. Malformed numeric values should be ignored with a diagnostic reason.
- If the request has `Authorization`, cache only when response directives allow it, such as `public`; add reason `authorization allowed by public` when that rule is used.
- Parse `Expires` when present. If it is malformed, add `invalid Expires header`. Return ordered `reasons`, `cacheable`, and `freshnessSeconds`, with freshness set to 0 when not cacheable.

## Verifier Constraints

- Keep the implementation deterministic and local to the Java project. Do not use `Runtime.getRuntime()`, `ProcessBuilder`, `java.net.http`, raw sockets, or `System.exit`.
- Keep each Java source file under 500 lines. Implement the general rules above rather than hard-coding complete verifier outputs.
- The CLI feature cases compare parsed JSON values, while bundled public Maven examples may compare exact strings. Emit deterministic JSON with lexicographically sorted object keys, and keep arrays, diagnostics, and selected ids in the ordering specified above.

## Evaluation

The verifier is fractional, not binary: it scores each capability separately
and combines them into an overall weighted score.
