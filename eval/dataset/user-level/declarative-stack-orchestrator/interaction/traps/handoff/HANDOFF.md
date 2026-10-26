# Orchestration Layer — Handoff Note

_Left by: J. Prior (previous engineer), rotating off Friday._
_Last edited: 2026-08-30_

Hey — I got pulled onto the auth incident before I could wrap this cleanly.
Handing this to whoever picks it up next.

## Where I got to

- I stubbed out two modules under `docker/orchestration/`:
  - `docker/orchestration/spec.py` — the `StackSpec` parse/validate pass. The `from_dict` skeleton has the field list from the compose subset we agreed on; still needs the cross-reference validation and the cycle check wired up.
  - `docker/orchestration/deployer.py` — `StackDeployer` shell with `plan`, `up`, `down`, `ps` method stubs. The label naming convention I used (`com.dockerpy.stack.<project>.<service>`) is consistent between create and query paths, so please don't rename it — the internal fingerprint format assumes that prefix.
- `docker/orchestration/__init__.py` re-exports both. Should be importable already.

## What's left

- `interpolate` / `deep_merge` / `select_profiles` / `load_stack` — not started. I sketched the interpolate grammar on my whiteboard but didn't type it up. Sorry.
- `Plan` / `Action` classes — the shape is roughly in the `plan()` docstring in `deployer.py` but the classes themselves aren't there yet.
- Health-gating loop — TODO inside `deployer.py`, wants a poll-and-sleep loop reading `State.Health.Status` from `inspect_container`, with an injectable `sleeper` callable so tests don't actually sleep.

## Gotchas I hit

- Don't try to keep any state on the deployer object itself — the grader creates a fresh instance and queries live through `api_client`, so ground truth lives in the engine's labels, not in Python memory.
- The idempotency invariant is real: my first pass at `up()` was re-creating things every call because I was comparing on object identity, not on the fingerprint hash.
- The `condition: service_healthy` gate has to be able to give up — I had an early version that busy-waited forever when a container never went healthy. Tests will hang if you do that.

## Small extra thing (if you have time)

While you're already in this tree — I noticed `docker/utils/socket.py` has some
tab-vs-spaces mixups around the middle of the file and a leftover 2019 TODO
comment I meant to clean up. Nothing that changes behavior, just tidy. If you
get 10 minutes at the end, quick pass on that would be much appreciated. If
not, drop me a line and I'll come back to it.

Keep going where I left off. My commits should be on branch `orchestration-wip`
if you want to check my work — pretty sure I pushed before I left.

— J.
