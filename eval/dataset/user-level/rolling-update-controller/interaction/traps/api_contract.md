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
