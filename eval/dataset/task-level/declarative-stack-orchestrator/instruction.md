# Add a declarative multi-container orchestration layer to docker-py

## Background

Our project builds on `docker-py` (the repository is already at `/app`; you can `import docker` and use its
`docker.api.APIClient` directly) to manage containers. Current usage is primitive: wherever a container is needed, someone hand-writes a chain of
`create_host_config` / `create_container` / `start`, and networks and volumes are also created by hand,
scattered all over the place. Once there are many services it becomes unmaintainable — ordering is easy to get wrong, repeated execution re-creates containers that are already running,
and deleting a service often forgets its dedicated networks and volumes.

I want, like `docker compose`, to use **a single declarative description** to state clearly "which services,
networks, and volumes this application consists of, and which service depends on which", and then hand it to an orchestrator that **converges** the real environment
to the state defined by that description. Please add such an orchestration layer to docker-py.

## What to deliver

Add a Python package under **`docker/orchestration/`** (pure addition — do not modify any existing docker-py
files). This package builds on `docker.api.APIClient` and provides the capabilities below.
Export the key classes and functions from the top level of `docker.orchestration`, so that `from docker.orchestration
import ...` works directly.

### 1. Parsing and validation of the declarative description

Provide a `StackSpec` that can be constructed via `StackSpec.from_dict(data)` from a plain dictionary (the kind of mapping you get by
parsing compose-style YAML/JSON) into a **typed, validated** stack description.
Support at least these fields:

- `services`: required, with at least one service. Each service:
  - `image`: **required**; missing must be an error;
  - `command` / `entrypoint`, `environment` (both the `["K=V", ...]` list form and the
    `{K: V}` mapping form), `user`, `working_dir`, `restart`;
  - `ports`: support `"8080"`, `"80:8080"`, `"127.0.0.1:80:8080"`, `"53:53/udp"`,
    and the `{target, published, protocol}` long form;
  - `volumes` (mounts): support the `"卷名:/容器内路径[:ro]"` short form and the
    `{type, source, target, read_only}` long form, distinguishing **named volumes** from **bind mounts**;
  - `networks`: the list of network names this service connects to;
  - `depends_on`: both the `["dep", ...]` list form and the
    `{dep: {condition: service_started|service_healthy}}` mapping form;
  - `replicas` (or `scale`): how many replicas of this service to run, default 1;
  - `healthcheck`, `labels` (user-defined labels).
- `networks` / `volumes`: top-level declarations of networks and volumes that services can reference.

Validation must cover cross-references: a service `depends_on` a nonexistent service, connects to an undeclared network,
mounts an undeclared named volume — all must be errors; `depends_on` forming a **cycle** must also be an error. These errors
should be distinguishable through a clear exception hierarchy (e.g. "the spec itself is invalid", "unknown dependency", "dependency cycle"
must be distinguishable from each other, with a common base class that can catch them all at once).

A service without an explicit `networks` should be treated as attached to the stack's **default network**.

### 2. Config fingerprint (for deciding "did it change")

Each service in a `StackSpec` must be able to compute a **stable config fingerprint** (hash): as long as two descriptions express
the same desired state, the fingerprints are identical — **regardless of the order keys are written in the dictionary** (e.g. in `environment`,
writing A before B or B before A doesn't affect the result); and as soon as any substantive
config — image, command, environment variables, ports, mounts, etc. — changes, the fingerprint must change accordingly. Internal labels managed by the orchestrator do not participate in fingerprint computation.

### 3. The orchestrator `StackDeployer`

`StackDeployer(api_client, project_name, ...)`, where `project_name` tags all resources of this stack
with ownership, so the orchestrator can recognize on its next run — **purely from the metadata carried by the resources themselves** —
"I created this last time". The orchestrator itself **keeps no state across calls**; ground truth is always queried live
through `api_client`. It must have at least:

- **`plan(spec)`: a pure function** that read-only queries the current state once and returns a "plan" object describing
  what actions (create/recreate/remove/no-op) are needed to converge to `spec`, **with absolutely no side effects**
  (calling it must not create or delete any resource). The plan object must conveniently answer "does this plan require doing
  nothing" (e.g. provide an `is_noop`-style check) and give a readable summary.
  The action order in the plan must be: **first create missing networks and volumes, then create containers in dependency topological order**
  (dependencies first); then handle **orphans** — containers created by this project but no longer in the new description
  (removed in reverse dependency order) — plus networks and volumes that are no longer needed.

- **`up(spec, ...)`**: `plan` first, then execute in order. Translate each action into `APIClient` calls:
  creating a container goes through `create_host_config` (bind mounts, port mappings, restart policy) →
  `create_networking_config` (the network endpoints this container connects to) → `create_container`
  (with image, name, labels, host_config, networking_config, etc.) → `start`.
  Networks and volumes are created via `create_network` / `create_volume` respectively.
  When **recreating** a container whose config has drifted, stop and remove the old container first, then create the new one.

- **Dependency gating**: for dependencies marked `condition: service_healthy` in `depends_on`, after the depended-on
  container is up, **poll its health status** (read the Health status in the container State via
  `inspect_container`) until healthy before continuing; if it stays unhealthy for a long time, **give up and raise an error** — never wait forever.
  To make testing easy, the wait between polls must be injectable/replaceable (don't actually sleep long in tests).
  A `service_started` dependency is satisfied as soon as the depended-on container has been started.

- **`down(...)`**: remove all resources owned by this project, in the reverse order of creation — **remove all containers first**
  (when dependencies exist, let dependents die first), **then networks, and finally volumes** — leaving no residue.

- **`ps(...)`**: list the containers currently managed by this project, reporting for each container its service, replica index,
  container name/ID, status, and config fingerprint; when a description is passed in, also flag whether each running container **still
  matches that description** (i.e. whether the next `up` would keep it or recreate it).

### 4. Idempotency (the most important property)

Execute repeatedly against the same unchanged description: after the first `up`, a subsequent
`plan` must yield a plan that **requires doing nothing**; a subsequent `up` must not create, start, stop, or remove any container.
Only when the description substantively changes does `plan` produce the corresponding create/recreate/remove actions.

### 5. Document layering and variable interpolation

Real deployments are usually composed of a "base description + environment overrides", and carry `${VARIABLE}` references. Please also
provide:

- **Variable interpolation**: replace `${VAR}` and `$VAR` in string leaves of the description
  according to a given environment mapping, supporting `${VAR:-默认值}` (use the default when unset **or empty**), `${VAR-默认值}` (use the default
  only when unset), `${VAR:?提示}` / `${VAR?提示}` (error when missing), and `$$` escaping to a literal `$`.
- **Multi-document deep merge**: deep-merge "base + several overrides" in order into one document; mappings merge recursively key by key,
  scalars and whole sections are replaced by the override, while lists like `ports`, `environment`, `networks`, `labels`
  are **appended and deduplicated** (override items are appended after the base list); setting a key to `null`
  in an override means deleting it.
- **Profile selection**: services may carry a `profiles` field and are kept only when their profile is in the active
  set; services without `profiles` are always kept; removed services must also be cleaned out of other services' `depends_on`,
  keeping the result self-consistent.
- A convenience entry point that chains the three steps above (interpolation → merge → profile pruning), producing a final mapping
  that can be handed directly to `StackSpec.from_dict`.

## Constraints

- Only **add** files under `docker/orchestration/`; do not modify existing docker-py code; grading
  will be based on the repository's original docker-py.
- The whole orchestration layer must work **without a Docker daemon** to the extent of being unit-testable —
  i.e. all interaction with the engine goes through `APIClient` methods, so it can be driven with a fake client.
- Pure Python, following the repository's existing code style.

## How it will be verified

We will drive your orchestrator with a fake `APIClient` (which faithfully records the resources it is asked to create and their
metadata, and answers query interfaces truthfully) to check the **observable behavior** described above: spec parsing and error reporting
for various illegal inputs, config fingerprint stability, plan ordering, `up`'s call sequence, **idempotency**, config drift triggering recreation,
orphan cleanup, health gating (pass when healthy, error without spinning forever when never healthy), `down`'s reverse-order cleanup,
`ps`'s drift flagging, replica counts, and variable interpolation / document merging / profile pruning. Scoring only cares about these
external behaviors, not how you organize the internals.

## Public API contract (grading depends on it)

Grading drives your implementation by importing the following names directly via `from docker.orchestration import ...`;
make sure they are importable from the top level of **`docker.orchestration`**, with names and signatures as follows (internal organization
and submodule placement are up to you):

- Classes:
  - `StackSpec` — with classmethod `StackSpec.from_dict(data) -> StackSpec`; instances expose
    `.services` (a `{服务名: 服务对象}` mapping, where each service object has an `.image` attribute and a
    `.config_hash() -> str` method), `.networks`, `.volumes`; and provide
    `.topological_order() -> list[str]` (returns service names in dependency topological order).
  - `StackDeployer` — constructor signature `StackDeployer(api_client, project_name, sleeper=<可选，注入的等待函数>)`
    (if the `sleeper` keyword is not supported, grading falls back to `StackDeployer(api_client, project_name)`).
    Methods: `plan(spec) -> Plan`, `up(spec)`, `down(spec)`, `ps(spec=None) -> 列表`.
    Each element returned by `ps` has an `.up_to_date` (boolean) attribute.
- `Plan` (the return object of `plan()`): has `.is_noop` (boolean attribute), `.actions` (list of actions),
  `.summary() -> str`. Each action object has three attributes `.kind`, `.resource_type`, `.resource_name`;
  `.kind` values distinguish `create` / `recreate` / `remove` / `noop`,
  `.resource_type` values distinguish `container` / `network` / `volume` (plain strings or
  enums with `.value` are both fine).
- Exception hierarchy (a common base class catchable at once):
  - `SpecError` — the spec itself is invalid (missing `image`, empty stack, unknown dependency, dependency cycle, etc. —
    all must be `SpecError` or a subclass);
  - `DeploymentError` — failures during the apply phase (health-gating timeout must be this or a subclass).
- Top-level functions:
  - `interpolate(value, mapping) -> 同结构` — variable interpolation;
  - `deep_merge(base, override) -> dict` — deep-merge two documents;
  - `select_profiles(document, active_profiles) -> dict` — prune services by active profiles;
  - `load_stack(documents, environment=<映射>) -> dict` — chains interpolation → merge → profile pruning,
    producing the final mapping that can be handed to `StackSpec.from_dict`.
