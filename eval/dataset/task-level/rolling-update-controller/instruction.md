# Add a safe rolling update / canary release controller to docker-py

## Background

Our services run via `docker-py` (the repo is already at `/app`; `import docker` gives you its
`docker.api.APIClient`) in the "one service = a set of container replicas" style. Right now **upgrading
versions** is all manual: stop and delete old replicas one by one, then create a batch of new ones from the new image. The problem is obvious —
once the new version comes up broken (e.g. it won't start, health checks never pass), we've usually already deleted most of the old replicas,
the service goes down entirely, and rolling back means scrambling to reassemble the old version.

I want a **release controller**: give it "what the service is currently running, what to upgrade to, by what strategy",
and it safely moves that one service from the current version to the target version — replacing in batches, health-gating each step,
**automatically rolling back to the previously working version as soon as the new version fails to become healthy**, and never leaving the service in a
half-dead intermediate state. Add such a controller to docker-py.

## Deliverables

Add a Python package under **`docker/rollout/`** (purely additive; do not modify any
existing docker-py files). It builds on `docker.api.APIClient`. Export the key classes and functions from the
`docker.rollout` top level, so `from docker.rollout import ...` gets them directly.

### 1. Revision and UpdatePolicy

Model "what to run" and "how to update" separately:

- **`Revision`**: a container-config description of one service version — `image` (**required**, missing must error),
  `command` / `entrypoint`, `environment` (supports both the `["K=V", ...]` list form and the
  `{K: V}` mapping form), `labels`, `ports`, `healthcheck`, `restart`, `user`,
  `working_dir`, etc. Constructible via `Revision.from_dict(data)`. Each `Revision` must be able to compute
  a **stable content fingerprint** (a short id): as long as two descriptions express the same version, the fingerprints
  match — **regardless of key order in the dict** (e.g. writing A before B or B before A in `environment` doesn't affect
  the result); any substantive config change such as image or environment variables changes the fingerprint. Whether two `Revision`s are equal
  is decided by this fingerprint. The controller relies on it to tell "what's currently running is already the target version".

- **`UpdatePolicy`**: the knobs for how an update proceeds; constructed via `UpdatePolicy.from_dict(data)` with
  **validation** —
  - `strategy`: `rolling` (default, replace in batches), `blue_green` (bring up the whole new set first, then switch),
    `recreate` (tear down everything and rebuild), `canary` (release a small batch of canaries first, evaluate, then go full);
    unknown strategies must error.
  - `batch_size`: how many replicas a rolling update replaces per batch (must be ≥ 1, otherwise error).
  - `health_attempts`: max probes before a replica is judged healthy (must be ≥ 1).
  - `require_health`: whether to health-gate new replicas (default on).
  - `auto_rollback`: whether to automatically roll back to the old version on update failure (default on).
  - `pause_between_batches`: seconds of pause between batches.
  - `canary_slots`: how many replicas the canary phase initially occupies with the new version.
  - `canary`: the canary evaluation policy (see section 4).

### 2. The `RolloutController`

`RolloutController(api_client, service_name, replicas=None, ...)`. The controller **keeps no
cross-call state**; which replicas currently exist and which version each runs are always queried live through `api_client`
(filtering `containers(...)` by the labels corresponding to the service name). It models a service as a set of labeled
replicas — the labels record at least the **service name, version fingerprint, replica index**, and, for blue-green releases, the
**color** (blue / green), so the controller can tell which service and version a replica belongs to from its own labels alone.
When `replicas` is not given explicitly, infer it from current state (a brand-new service defaults to 1). It must have at least:

- **`plan(revision, policy)`: a pure function** that queries current state once, read-only, and returns a "plan"
  object describing which **batches** of which steps (creating new replicas, deleting
  old replicas, health checks, blue-green switching, etc.) are needed to advance to `revision`, **with absolutely no side effects** (calling it must not create or delete
  any container). When every desired replica is already on the target version and the replica count is correct, the plan should be a
  **do-nothing** plan (provide an `is_noop`-style check), and be able to report the batch count and a readable summary.

- **`rollout(revision, policy)`**: first `plan`, then execute batch by batch; returns an object recording the outcome of this release
  (whether it succeeded, the final version, how many batches completed, whether a rollback happened, which replicas were created/deleted, etc.).
  Translate each step into `APIClient` calls: creating a replica goes through `create_host_config` →
  `create_container` (with image, name, labels, etc.) → `start`; retiring a replica goes through `stop` +
  `remove_container`. Semantics per strategy:
  - **rolling**: split the replicas needing update into batches of `batch_size`; each batch first deletes the old, then creates the new, then health-gates
    these new replicas; only after they pass does it move to the next batch; when the replica count doesn't match the target, scale up/down accordingly.
  - **blue_green**: first bring up **an entire set** of new-version (new color) replicas and health-gate them, then have a
    **promote (switch)** step; only after the switch are the old set deleted — i.e. all creation happens before the switch,
    and deletion of old replicas happens after it.
  - **recreate**: first delete all current replicas, then build the target version from scratch.
  - **canary**: see section 4.

- **Health gating**: when `require_health` is on, after creating new replicas **poll their health status** (read the Health status in the container State via
  `inspect_container`), continuing only once healthy; if still unhealthy after
  `health_attempts` probes, judge it failed — never wait forever. To ease testing, the wait between polls must be injectable/replaceable (tests must not really sleep).

- **Automatic rollback (the most critical property)**: when `auto_rollback` is on and some step fails during a release
  (typically the new version never becomes healthy), the controller must **restore the service to the pre-release version** before raising an exception
  — the old-version config used for restoration should be read live from the old replicas still running at that moment
  (`inspect_container`'s Config). After rollback completes, the service should **run only the old version**, with the replica count
  the same as before the release; the raised exception must let the caller tell that "a rollback already happened". A rollback itself must not trigger nested
  rollbacks. If `auto_rollback` is off, failures fail as-is with no restoration.

### 3. Idempotency

Operating repeatedly against the same unchanged target version: after one successful `rollout`, another `plan` must yield
a **do-nothing** plan; another `rollout` must not create, start, stop, or delete any
replica. Only when the target version changes substantively does `plan` produce corresponding actions.

### 4. Canary releases and metric evaluation

A canary release doesn't just look at "did the container come up", but at whether the new version performs acceptably under
**real metrics**. Provide a metric evaluation:

- Metrics themselves come from outside this library (Prometheus, log pipelines, etc.), so evaluation should be parameterized by a **metric provider**
  — any callable that, given a canary replica name and the measurement index, returns a
  `{指标名: 值}` mapping. This way the library binds to no monitoring backend, yet can still make real promote / reject decisions.
- Provide a configurable evaluation policy (e.g. constructible from a dict), supporting at least: several **metric threshold checks**
  (metric name, threshold, comparison such as "less than / less-or-equal / greater than / greater-or-equal"), the **number of intervals** of consecutive measurements,
  and a tolerable failure count. The canary must **continuously satisfy** all checks across these intervals
  to pass; once it crosses a line it should **fail as early as possible** (no need to run the remaining intervals). With no metric checks configured, it passes unconditionally.
- The `canary` strategy's `rollout`: first use `canary_slots` to bring up a small batch of new-version replicas and health-gate
  them, then have an explicit **analyze** gate running the metric evaluation above; only if evaluation passes does it continue
  migrating the remaining replicas over like a rolling update; if evaluation fails, the release is considered failed (with `auto_rollback` on,
  rollback triggers as well, leaving the service on the old version).

### 5. History and explicit rollback

The controller must record the **revision-change history** it has driven (deduplicated: recording the same version consecutively doesn't count repeatedly),
and on that basis support:

- querying history (the version of each change, image, whether currently active, etc.), plus the current / previous version;
- **explicit rollback**: `rollback_to(revision)` actively moves the service back to a known old version (this is a
  normal, health-gated release, but the rollback process itself does not auto-nest further rollbacks); `rollback_steps(n)`
  rolls back n changes along the history.

### 6. Scaling (without changing version)

Provide `scale(count)` (and its corresponding pure `plan_scale(count)`): adjust the replica count to `count` **without changing the version**
— when scaling up, fill in new replicas at the currently running version; when scaling down, retire the replicas with the largest
indices first; already at the target count is a no-op. Scaling and version release are two independent things, not to be conflated.

### 7. Declarative release manifest (optional entry point)

Real releases are often written as **a manifest** (committed to the repo, reviewed, applied by CI). Provide a
`RolloutSpec` that parses a service name,
replica count, target `Revision`, and `UpdatePolicy` from a dict (the parsed result of compose-style YAML/JSON), and performs
**cross-validation** — e.g. `canary_slots`
must not exceed the replica count, only the `canary` strategy may carry a canary evaluation block,
`max_unavailable` must not
exceed the replica count; self-contradictory manifests must error. All the illegal inputs above should be distinguishable
from the legal path through a clear exception hierarchy.

## Constraints

- Only **add** files under `docker/rollout/`; do not modify existing docker-py code; grading is based on the repo's
  pristine docker-py.
- The whole controller must work, to a unit-testable degree, in an environment **without a Docker daemon**
  — i.e. all engine interaction goes through `APIClient` methods, so it can be driven by a stand-in client.
- Pure Python, following the repo's existing code style.

## Verification

We will drive your controller with a **stateful stand-in `APIClient`** (faithfully recording the containers it was asked to create along with their images and labels,
and returning them truthfully via the query interfaces; health status is modeled **per image**, so it can distinguish "the new version is broken" from "the whole world
is broken"), checking the **observable behavior** described above: stability of version fingerprints, errors for illegal
revisions / policies / manifests, first release and replica counts, plan purity and idempotency, rolling batching,
**automatic rollback to the old version** when the new version is unhealthy (with the service left running only the old version and the replica count restored), honest failure with auto-rollback off,
blue-green's "create first — switch — then delete" order, recreate's "delete everything first — then rebuild" order, canary
metrics passing → full rollout / failing → reject and roll back, the threshold judgments and early failure of metric evaluation, history recording and explicit
rollback, and scaling. Grading only cares about these external behaviors, not how you organize the implementation internally.

## Public API contract (grading depends on it)

To remove ambiguity, the grading script calls your package **with the exact names and signatures below**. The sections above describe behavior;
this section pins down the "externally visible symbol names, method names, attribute names, enum values, keyword arguments, exception types" one by one
— as long as the behavior matches the descriptions above and the external names match this section, you pass. The internals (module layout,
private classes, label key names, etc.) are unrestricted.

**Import paths**: all public symbols below must be importable directly from the `docker.rollout` top level
(e.g. `from docker.rollout import RolloutController, Revision, UpdatePolicy,
RolloutSpec, CanaryAnalysis, Comparison, MetricCheck, RevisionError,
RolloutFailed`).

### The `Revision` version
- `Revision.from_dict(data)` → `Revision`; missing `image` raises `RevisionError`.
- `rev.revision_id()` (**method**) → stable content fingerprint string; independent of dict key order; changes when substantive config changes.
- `rev.image` → the image string (attribute).
- `==` / `!=` between `Revision`s is decided by `revision_id()`.

### Update policy `UpdatePolicy`
- `UpdatePolicy.from_dict(data)` → `UpdatePolicy`; illegal input raises `RevisionError`
  (unknown `strategy`, `batch_size` < 1, etc.).
- `data` supports keys: `strategy`, `batch_size`, `health_attempts`, `require_health`,
  `auto_rollback`, `pause_between_batches`, `canary_slots`, `canary`,
  `max_unavailable`.
- `strategy` values are strings: `"rolling"`, `"blue_green"`, `"recreate"`, `"canary"`.
- Attributes: `policy.strategy` (string, equality-comparable e.g. `policy.strategy == "canary"`),
  `policy.canary` (the canary evaluation object or `None`).
- When `canary` is constructed from a dict, `policy.canary.checks` is the parsed list of metric checks (used to tell
  whether canary checks are configured; an empty list means unconfigured).

### Exception hierarchy
- `RevisionError`: raised when a revision / policy / manifest description is illegal or self-contradictory.
- `RolloutFailed`: raised when a release cannot complete; carries a boolean attribute `exc.rolled_back`, which is
  `True` when `auto_rollback` triggered and completed a rollback, otherwise `False`.
- Both must be importable from the `docker.rollout` top level (when missing, grading degrades to `ValueError` /
  `Exception`, but please provide them faithfully for precise distinction).

### Controller `RolloutController`
- Constructor: `RolloutController(api_client, service_name, replicas=None,
  sleeper=None, metric_provider=None)`.
  - `sleeper`: an injectable wait function (signature `sleeper(seconds)`); all waits in health gating / canary intervals
    go through it; tests pass in a stand-in that "doesn't really sleep"; do not really
    `time.sleep` on test paths.
  - `metric_provider`: a callable with signature `metric_provider(canary_name,
    interval_index)` → `{指标名: 值}` mapping, used by the canary strategy.
  - All three keyword arguments above are optional (the grader passes them as keywords, and may pass only the first two positional arguments).
- `ctl.plan(revision, policy=None)` → plan object (pure function, no side effects).
- `ctl.rollout(revision, policy=None)` → `RolloutResult`; raises `RolloutFailed` on failure.
- `ctl.scale(count)` → `RolloutResult`.
- `ctl.plan_scale(count)` → plan object.
- `ctl.history()` → history object (see below).
- `ctl.rollback_steps(steps=1)` → `RolloutResult` (the keyword argument name is `steps`); returns `None` when history
  isn't deep enough to roll back.
- `ctl.rollback_to(revision, policy=None)` (explicit rollback to a specified version).
- The `revision` parameter accepts a `Revision` or a dict parseable by `Revision.from_dict`; `policy`
  likewise may be an `UpdatePolicy` or a dict or `None` (use the default policy).

### Plan object (returned by `plan()` / `plan_scale()`)
- `plan.is_noop` → boolean **attribute** (not a method): `True` when no action is needed.
- `plan.batch_count()` → **method**, returns the batch count (integer). E.g. 4 replicas with `batch_size=2`
  rolling update should be 2.
- `plan.steps` → the step list; each step has an attribute `step.kind`.
- `step.kind` is an enum member or string; the grader takes its string value via `getattr(step.kind, "value",
  step.kind)`; values include at least: `"create"`, `"remove"`, `"promote"`
  (the blue-green switch step), `"analyze"` (the canary evaluation gate); it may also have `"health"`, `"pause"`, etc.
  - A blue-green plan must contain one `"promote"` step, with all `"create"` before it and `"remove"` of old replicas
    after it.
  - In a recreate plan, all `"remove"` must come before any `"create"`.
  - A canary plan must contain an `"analyze"` step.

### Release result `RolloutResult`
- `result.ok` → boolean attribute: `True` on success.

### Canary evaluation `CanaryAnalysis` / `MetricCheck` / `Comparison`
- `CanaryAnalysis.from_dict(data)`: `data` supports `intervals` (number of intervals) and
  `metrics` (list of checks, each `{"name", "threshold", "comparison"}`, with `comparison`
  values like `"lt"`/`"le"`/`"gt"`/`"ge"`).
- `analysis.run(provider, canary_name, sleeper=None)` → result object with attributes:
  - `.passed` (boolean): `True` when all intervals satisfy the checks.
  - `.intervals_run` (integer): the number of intervals actually run; crossing a line should **fail as early as possible**, so failing on the first interval
    should give `1`.
- With no metric checks configured (`metrics` empty), it passes unconditionally.
- `Comparison` and `MetricCheck` must also be importable from the top level.

### History object (returned by `ctl.history()`)
- `hist.entries()` → **method**, returns the list of history entries; each entry has an attribute `entry.image` (the image
  string of that change).
- `hist.current()` → **method**, returns the currently active entry; its `.image` is the current image.
- Recording the same version consecutively doesn't count repeatedly (deduplicated).

### Declarative manifest `RolloutSpec`
- `RolloutSpec.from_dict(data)` → `RolloutSpec`; illegal / self-contradictory manifests raise
  `RevisionError` (e.g. `canary_slots` > `replicas`, a non-canary strategy carrying a canary evaluation block,
  `max_unavailable` > `replicas`, etc.).
- `data` top-level keys: `version`, `service`, `replicas`, `revision`, `policy`.
- Attributes: `spec.service` (string), `spec.revision` (a `Revision`; `.image` usable),
  `spec.policy` (an `UpdatePolicy`; `.strategy`, `.canary.checks` usable).
