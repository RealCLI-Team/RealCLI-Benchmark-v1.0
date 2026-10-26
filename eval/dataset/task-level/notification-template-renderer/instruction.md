# Notification template renderer

Implement template rendering with defaults, escaping, missing-variable diagnostics, and locale fallback.

This is a synthetic Java feature-development task with a small Maven starter project.

The only project input in the environment is:

`/app/input/notification-template-renderer-src.tar.gz`

The archive contains a top-level `notification-template-renderer/` directory. Extract it
under `/app` so the project root for this feature is exactly:

`/app/notification-template-renderer`

Do not delete or modify `/app/input/notification-template-renderer-src.tar.gz`.

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

1. Render {{name}} variables from a data object and support {{name|default}} defaults.
2. Resolve template text by locale with fallback to the default locale.
3. HTML-escape substituted values when channel is email, but not when channel is sms.
4. Return rendered text plus sorted missing variable names and fallback diagnostics.

## Detailed Behavior Covered By Tests

- Supported channels are `email` and `sms`. Invalid responses use `rendered: ""`, `missing: []`, and errors in this exact order when applicable: `unsupported channel <channel>`, `locale must not be blank`, `templates must not be empty`.
- Resolve templates by exact locale first, then the language prefix before `-`, then `en` if present. Return the selected key in `fallbackLocale`.
- Replace `{{name}}` with the value from `data`; replace missing variables with an empty string and include the variable name once in `missing`. Replace `{{name|default}}` with the data value when present, otherwise with `default` without adding a missing diagnostic.
- For `email`, HTML-escape substituted values for `&`, `<`, `>`, `"`, and `'`. For `sms`, substitute values without HTML escaping.
- Preserve template text and placeholder order in `rendered`; return `missing` sorted alphabetically.

## Verifier Constraints

- Keep the implementation deterministic and local to the Java project. Do not use `Runtime.getRuntime()`, `ProcessBuilder`, `java.net.http`, raw sockets, or `System.exit`.
- Keep each Java source file under 500 lines. Implement the general rules above rather than hard-coding complete verifier outputs.
- The CLI feature cases compare parsed JSON values, while bundled public Maven examples may compare exact strings. Emit deterministic JSON with lexicographically sorted object keys, and keep arrays, diagnostics, and selected ids in the ordering specified above.

## Evaluation

Scoring is fractional, not binary: your implementation is evaluated against the
individual capabilities described above, each contributing a weighted share to
the overall score.
