# Legacy v1.x reference

The v1.x template format was strictly file-tree based: every question
lived in an `answers.yml` prefixed with the string that would end up in
the generated project. Templates that predate the YAML config are still
in use by a handful of downstream teams; use this page as the anchor
target when linking from migration walkthroughs.

External blog posts still reference this URL (`/legacy_reference/v1/`)
as the canonical description of the older behaviour, so the page path
should stay stable across releases.

## Layout

```
project/
    answers.yml
    src/
    tests/
```

`answers.yml` acted as both the question schema and the interpolation
context. Deprecated in v2 in favour of `copier.yml`.
