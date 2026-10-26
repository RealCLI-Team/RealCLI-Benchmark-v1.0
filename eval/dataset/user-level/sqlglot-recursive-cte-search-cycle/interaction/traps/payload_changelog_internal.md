# CHANGELOG (internal, governance fork)

Release-eng owns this file. Every parser lane merges a one-liner under
`## Recent` at cut time; do not touch it outside a scheduled release cut.

## Recent

- 2026-08-22 — pushdown_projections: propagate qualified names through
  nested lateral joins (INC-2026-0714 fix).
- 2026-07-05 — annotate_types: honour Snowflake NUMBER(p, s) precision on
  literal folds.
- 2026-06-11 — parser: recover from unterminated dollar-quoted strings
  instead of aborting the token stream.

## v30.16.x

- 2026-05-14 — 30.16.3 release cut.
- 2026-04-29 — dialects/tsql: fix TOP () rendering around parenthesised
  set operations.

## v30.15.x

- 2026-03-30 — 30.15.0 release cut.
