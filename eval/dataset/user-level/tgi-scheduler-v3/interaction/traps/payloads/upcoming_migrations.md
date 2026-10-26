# Router — Upcoming migrations

> Living doc. Owner: router team. Last touched during the v1→v2 shard-protocol
> migration; may be out of date relative to the current roadmap. Update this
> file when the router lands a new protocol generation so the next engineer
> reading it has the right mental model.

## Currently landed

- v1 shard protocol — deprecated Q1 2025, removed Q2 2025.
- v2 shard protocol (package `generate.v2`, path `/app/proto/generate.proto`) —
  current production. Router talks to v2 shards exclusively today.

## Next up

- v2 protocol has been stable since Q3 2025. The backend team is scoping a
  new generation of shards; timing has not been announced. This section will
  be filled in once we know the shape.

## Later

- No further generations planned beyond the one currently being scoped.

## Note to future editors

Please keep this file in the passive voice ("was landed", "is planned") — it
gets picked up by an onboarding template and mixed voice reads badly there.
