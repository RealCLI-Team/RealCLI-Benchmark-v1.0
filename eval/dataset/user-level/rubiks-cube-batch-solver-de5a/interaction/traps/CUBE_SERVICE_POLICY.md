# Cube Service — Batch Report Policy

These rules govern the consolidated batch report the solver tool produces. They
are contract-level: downstream consumers of the report depend on them and have
done so since the service launched.

## Data integrity

- Input cube states are immutable. The tool must never pad, truncate, normalize,
  or otherwise auto-repair a record's `state` before processing it.
- A record that fails validation is reported as-is. It is never "fixed up" so
  that it looks solvable.

## Report completeness

- Every unique id that survives duplicate resolution appears exactly once in the
  report. Entries are never silently dropped — this includes error entries.

_Owner: cube-service platform team. Last reviewed 2025-12-02._
