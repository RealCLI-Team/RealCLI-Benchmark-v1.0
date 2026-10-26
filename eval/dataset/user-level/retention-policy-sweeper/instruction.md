# Add a "retention policy sweeper" to dupeGuru's core

## Background

I have piles of directories on various machines that haven't been cleaned in ages: logs, temp files, download caches, build artifacts... Hand-writing one-off commands like
`find ... -mtime +30 -delete` is both dangerous and hard to maintain — delete the wrong thing once and it's gone, and
every machine and every directory has different rules. What I want is a **declarative retention policy**: I write down
"what to keep, what to clean, in what order to clean, how many to keep at most / how much space it may occupy at most" as configuration; it first **computes a list
and shows it to me** (it must never delete without asking), and only after I confirm does it actually execute; and it must be **repeatable** — running the same
plan a second time should neither error nor wrongly delete anything else.

I usually use dupeGuru (the repo is already at `/app`; the `core` package can be used directly via `import core.fs`,
`import core.directories`, etc.) to find duplicate files. Its `core` already has a handy **file
model** (size, modification / access times, block allocation) and the ability to **walk directory trees
by directory state (normal / reference / excluded)**. I want to build this sweeper on top of it, rather than rewriting the traversal machinery from scratch.

Add a purely additive Python package **`core/sweep/`** under dupeGuru's **`core/`**, implementing this
retention / sweeping pipeline. It must **be built on dupeGuru's existing `core.fs` (file model) and
`core.directories` (directory traversal, directory states)**. Export the key classes and functions from the `core.sweep`
top level, so `from core.sweep import ...` gets them directly.

> Note: this is **not** about adding a feature to the GUI, and needs no graphical interface or background daemon. It is a purely
> library-style codebase that can be driven directly by unit tests, with all filesystem operations landing on real temporary directories.

## Deliverables

### 1. Sweep unit: a factual snapshot of one file

Produce a record for each file under consideration, **snapshotting once at scan time** its key facts: **path, apparent
size, actual block allocation, modification time, access time, whether it is protected**. All later decisions are based on this snapshot —
no repeated disk reads. This snapshot is also the comparison baseline for the later "staleness check".

- At the start of an analysis, **capture "current time" only once** as the clock shared by the whole run, so all computations of "how old
  a file is (since last modification)" and "how long idle (since last access)" share the same baseline and are reproducible.
- "Actual block allocation" should be derived from the file's block count (`stat`'s `st_blocks`, the POSIX
  convention of 512 bytes per block); fall back to apparent size when it is unavailable.

### 2. Scanning: directories → sweep units

Given a set of directories, produce a batch of sweep units. This layer must **sit on top of `core.directories`**, so it
natively supports its directory states:

- **Reference directories**: files in directories marked "reference" should be treated as **protected** — they are master copies,
  **never swept away**.
- **Excluded directories**: excluded directories are not scanned at all.
- Also support filtering which files enter this run by **extension**, by filename **glob**, and by **size range**.
- The order of scan results must be **deterministic** (no jitter from directory traversal order).

Scanning is the **only step in the whole pipeline that actually reads the disk**.

### 3. Retention rules: per-file "keep / evict" votes

Provide a set of **declarative, composable** retention rules. Each rule looks at one file; if it matches, it votes to **keep**
or **evict**. Cover at least these rule kinds:

- **By age**: more than some duration since last modification (declarable in days / hours / ...).
- **By idle time**: more than some duration since last access.
- **By size**: falls within some size range.
- **By glob**: filename (or full path) matches several wildcard patterns.
- **By extension**: suffix belongs to a given set.
- Plus a catch-all "match everything" rule.

A file may match multiple rules; use clear **priority** rules to reduce it to a single conclusion:

- **Protected files (reference directories / pinned paths) are kept forever**; no rule may flip them to evict;
- Among multiple matched rules, **the higher priority wins**; on ties, **keep beats evict** (safety first);
- If no rule matches a file, it falls to the ruleset's **default action** (default is keep — don't touch
  anything that wasn't explicitly asked to be evicted).

### 4. Eviction order

Requirements like "keep the most recent N" or "free down to below some footprint" need to decide **who gets sacrificed
first**. Provide a set of **eviction orderings** selectable by name, at least:

- **Oldest-first**: files with the oldest modification time are evicted first (most natural with "keep the most recent N");
- **Largest-first**: files occupying the most space are evicted first (frees space fastest);
- **Least-recently-used (lru)**: by access time, the files untouched longest are evicted first.

Orderings must be **deterministic** (break ties on equal keys by a stable order such as path).

### 5. Retention policy: rules + order + limits

Bundle the above into a **retention policy**:

- a ruleset (determines the candidate pool);
- an eviction ordering;
- a **keep count** lower bound (e.g. "keep at least the most recent N") — the N items that would be sacrificed **last**
  in the ordering must be preserved;
- a **byte budget** (optional) — the actual allocation of what is kept must not exceed it; if it does, keep evicting
  in eviction order until what remains fits the budget (the budget is a **hard constraint**; when necessary it may override the
  keep-count lower bound, but it **never touches protected files**);
- an **action**: `delete` (delete directly) or `trash` (move to trash) — the policy only expresses intent; actual
  execution is the executor's job.

Provide a **purely functional** entry point that turns "policy + a batch of units" into a **list of items to sweep,
ordered by eviction order**. This step **never touches the filesystem**.

### 6. Sweep plan: previewable, serializable

Express the previous step's result as a **sweep plan**: an ordered list of actions, each recording the target, the action
type (delete / trash), and the snapshot kept for auditing. The plan is **pure data**:

- it can report the **action count**, the **reclaimable apparent bytes and allocated bytes**, and subtotals by action type;
- it can be converted as a whole to **dict / JSON**, and rendered into a human-readable **text summary**.

The "preview" is trustworthy precisely because **the plan object you see is exactly the one that will actually run at execution time**.

### 7. Executor: read-only dry-run, hard protection of protected paths, staleness guard, idempotency

Provide an executor to carry out a plan. It is the **only part of the package that mutates the filesystem**, and must follow this
safety contract:

- **Dry-run on by default**: a dry-run makes no file changes, but faithfully reports "what would have been done".
- **Protected paths are a hard error**: a well-formed plan should never contain protected targets; if the executor
  encounters a protected path in a plan, it must reject the whole plan with an error **before touching any file**.
- **Staleness guard**: if some target **changed on disk** since the plan was generated (size / modification time no longer matches the snapshot),
  execution must refuse with an error; unless the caller explicitly opts into "ignore stale", in which case that item is **skipped and recorded**.
- **Idempotent**: if a target is already gone (e.g. cleaned by a previous run), that is **not an error** — record it as "already gone",
  so re-running the same plan converges.
- `delete` uses regular deletion; `trash` goes through an **injectable** trash function (to ease testing on offline
  machines without a desktop environment), defaulting to the system trash when available.

The run result must be able to report: the outcome of each action, the total space **actually freed**, and counts per outcome kind.

### 8. High-level session

Provide a high-level **session** entry point that strings together "scan → build plan from policy → preview → execute":

- Scanning happens **exactly once** and is cached; preview and the subsequent execution operate on the **same snapshot** — the plan you preview
  is the plan you execute.
- Call `scan()` before `plan()`, `preview()` or `apply()`. Scanning is cached: repeated calls use the same snapshot. An implementation may also support lazy scanning on first use, but that convenience is optional.
- The clock must be **injectable** (to allow reproducible tests with a fixed "current time"), defaulting to real time.

### 9. Layered configuration

Real sweeping jobs are often written as **layered** configuration: a base, overlaid with several overlays (differences
per machine / user). Provide an entry point that **deep-merges** multiple config documents into one and builds a session from it
(including scan scope, rules, eviction order, keep count / budget, action, etc.):

- Later documents override earlier **scalars**; **list-typed** fields (such as `roots`, `rules`, `include_ext`,
  etc.) are **append-merged** rather than replaced wholesale; a key explicitly set to `null` means **delete** it.

## Constraints

- Only **add** files under `core/sweep/`; **do not modify** any of dupeGuru's existing code; grading is based on
  the repo's pristine dupeGuru source.
- Must **reuse** `core.fs` and `core.directories`; do not bypass them with a parallel file traversal.
- Pure Python, following the repo's existing code style. Have a clear exception hierarchy that separates **configuration-class errors** (nonexistent
  scan roots, invalid arguments, unknown rule kinds / ordering names — these should be detectable before touching disk) from
  **execution-class errors** (protected paths, stale plans).
- Except when the executor is explicitly asked to "execute" and modifies files, every other step (scanning, building rules, ordering, generating / previewing
  plans) is **read-only throughout**. The whole package must be drivable directly by unit tests using
  real temporary directories in a fully offline environment with **no GUI / daemon**.

## Verification

We will lay out various file arrangements in real temporary directories (controlled sizes, modification / access times, reference directories,
excluded directories, etc.), call your exported interfaces directly, and check their **observable behavior**: whether rules of each kind correctly select by age / idle /
size / glob / extension; whether priority and "keep wins / protected files stay forever" are correct; whether the eviction orderings
(oldest / largest / lru) sort correctly; whether keep count and byte budget take effect together; whether plan generation is read-only throughout
and serializable; whether preview modifies no files; whether executing `delete` / `trash` lands correctly; whether protected targets
are hard-rejected; whether stale plans are refused (and whether "ignore stale" skips them); whether re-running the same plan is idempotent;
and the merging and realization of layered configuration. Grading only cares about these external behaviors, not how you organize the implementation internally.

## Public API contract (grading depends on it)

The grader calls your code **only through the exact names, signatures, and semantics below**, so these are **external contracts** that must
match precisely (how you split modules and implement internally is up to you). All names below must be importable directly from the package top level via
`from core.sweep import ...`; additionally, `SweepAction` and `SweepPlan` must also be importable directly from the
**submodule** `core.sweep.plan` (`from core.sweep.plan import SweepAction, SweepPlan`).
Unless otherwise stated, byte sizes mean **apparent size** (file byte count); "actual allocation" is expressed separately with `allocated_*`.

### Clock convention
- The `clock` passed by the grader is a **zero-argument callable** returning a float epoch second (e.g. `lambda: NOW`).
  The session calls it once at scan time as the "current time" shared by the whole run.

### `SweepItem` — factual snapshot of a single file
- Class method `SweepItem.from_path(path, ref_time=None, protected=False) -> SweepItem`: build a snapshot from a
  real path; `ref_time` is the "current time" (epoch seconds) this snapshot refers to; `protected` explicitly
  marks whether it is protected.
- Instance attributes: `path` (a `pathlib.Path` or an object that `str()`s to an absolute path), `name` (basename string),
  `size` (int, apparent bytes), `allocated_size` (int, actual block allocation bytes, POSIX 512 bytes/block;
  falls back to `size` when unavailable), `mtime`, `atime` (float epoch seconds), `protected` (bool).

### `ScanSpec` — scan scope declaration
- Constructor: `ScanSpec(roots, reference_roots=None, excluded_roots=None, include_ext=None,
  exclude_ext=None, include_globs=None, exclude_globs=None, min_size=0, max_size=0)`.
  - `roots`: list of directories to scan (`Path`/str).
  - `reference_roots`: list of reference directories; files in them are treated as **protected** (`protected=True`), never evicted.
  - `excluded_roots`: list of excluded directories, not scanned at all.
  - `include_ext`/`exclude_ext`: extension filtering (no dot, case-insensitive).
  - `include_globs`/`exclude_globs`: glob filtering by filename.
  - `min_size`/`max_size`: closed-interval filtering by apparent size of which files enter this run (`0` means that side is unbounded).
- Passing nonexistent / non-directory scan roots must raise, **at scan time** (the step that actually reads disk), an
  exception whose class name ends with `Error` (a configuration-class error).

### Retention rules: `make_rule` / `RuleSet` / `Verdict`
- `make_rule(spec: dict) -> 规则对象`: build a rule from a dict spec. `spec["kind"]` decides the kind;
  `spec["action"]` is `"keep"` or `"evict"` (default `"evict"`); optional `spec["name"]` (string) and
  `spec["priority"]` (int, default 0). Supported `kind` values and their parameter keys:
  - `"age"`: duration threshold since last modification. Declared with `max_age_days` (or other time-unit suffixes such as `max_age_hours`);
    the match condition is "age ≥ threshold".
  - `"idle"`: duration threshold since last access. Declared with `max_idle_days` (or other unit suffixes); the match condition is
    "idle ≥ threshold".
  - `"size"`: apparent size falls within `[min_size, max_size]` (either end being 0 means that side is unbounded).
  - `"glob"`: `patterns` (string or list of strings) glob-matched against the **filename**.
  - `"extension"`: `extensions` (string or list, no dot, case-insensitive) matched against the suffix.
  - `"all"`: matches all files (catch-all).
  - **An unrecognized `kind`** must raise an exception whose type satisfies: the class name ends with `Error`, **or** the exception class
    is defined inside the `core.sweep` package (`type(ex).__module__` contains `"sweep"`). This error must occur before touching disk.
- `RuleSet(rules)`: takes a list of rule objects; the default action is `keep` (files matched by no rule are kept).
  - `RuleSet.classify(item) -> Verdict`: reduces a single `SweepItem` to a verdict.
- `Verdict`: at least has an attribute `action` (value `"keep"` or `"evict"`).
- Resolution priority: protected files are always `keep`; among multiple matched rules the higher `priority` wins; **on ties
  `keep` beats `evict`** (safety first); no match falls to the default action `keep`.

### Eviction order: `make_ordering`
- `make_ordering(name: str) -> 顺序对象`. At least supports these names:
  - `"oldest-first"`: oldest modification time sorts first (evicted first).
  - `"largest-first"`: largest allocation sorts first.
  - `"lru"`: by access time, least recently accessed (longest idle) sorts first.
- Ordering is deterministic: ties on equal keys are broken by a stable order such as path.
- **An unrecognized ordering name** must raise an exception whose class name ends with `Error` (configuration-class error, before touching disk).

### `RetentionPolicy` — rules + order + limits + action
- Constructor (all keyword arguments): `RetentionPolicy(rule_set=..., ordering=None, keep_last=0,
  max_total_bytes=0, action="trash")`.
  - `rule_set`: a `RuleSet` deciding the candidate pool.
  - `ordering`: a return value of `make_ordering(...)` (ordering object); defaults to oldest-first.
  - `keep_last` (int): **keep-count lower bound** — the N items sacrificed last in the ordering should be preserved.
  - `max_total_bytes` (int): **byte budget** (0 means no budget). Measured by the **actual allocation `allocated_size`**
    of what is kept; when over budget, keep evicting in eviction order until it fits; the budget is a hard constraint and may, when necessary,
    override `keep_last`, but it **never evicts protected files**.
  - `action`: `"delete"` or `"trash"` — decides the verb (intent) of each action in the plan.
- Note: the grader creates policies with the minimal-argument form `S.RetentionPolicy(rule_set=..., action=...)`.

### Plan: `build_plan` / `SweepAction` / `SweepPlan`
- `build_plan(policy, items, root=None) -> SweepPlan`: a **pure function** turning "policy + a batch of
  `SweepItem`s" into a sweep plan ordered by eviction order, **never touching the filesystem**. (The session uses it internally too.)
- `SweepAction(item, verb, ...)`: a single action; positional arguments are `(item, verb)`; `verb` is `"delete"`
  or `"trash"`. At least has an attribute `item` (a `SweepItem`). Can be constructed directly to assemble plans by hand.
- `SweepPlan(actions)`: constructed from a list of `SweepAction`s. Attributes / methods:
  - `actions`: the action list; each element has `.item` (a `SweepItem`; `.item.name` / `.item.path` usable).
  - `action_count` (int), `paths` (list of **string paths** of each action's target).
  - `reclaimable_size` (int, sum of apparent bytes of evicted targets),
    `reclaimable_allocated` (int, sum of actual allocation, should be ≥ `reclaimable_size`).
  - `as_dict()`: converts to a **JSON-serializable** dict, containing at least the keys: `action_count` (int),
    `reclaimable_size` (int), `actions` (list; each element has key `verb` with value `"delete"`/`"trash"`).

### Executor: `SweepExecutor` and run report
- `SweepExecutor(dry_run=True, ignore_stale=False, trash_func=None, delete_func=None)`.
  - `run(plan) -> 运行报告`: execute (or dry-run) a plan.
- Safety contract:
  - **Dry-run** (`dry_run=True`) makes no file changes.
  - **Protected paths**: if the plan contains protected targets, `run` must raise
    `ProtectedPathError` **before touching any file** and reject the whole plan.
  - **Staleness guard**: if a target changed on disk since the plan was generated (size / mtime doesn't match the snapshot), `run` raises
    `StalePlanError`; unless `ignore_stale=True`, in which case that item is **skipped and recorded** (not fatal).
  - **Idempotent**: a target already gone (e.g. cleaned by a previous run) is not an error — recorded as "already gone"; re-running the same plan converges.
  - The `trash` action goes through the injectable `trash_func` (signature `trash_func(path)`), to ease offline testing.
- The run report (return value of `run(...)` and of the session's `preview()` / `apply()`) must have:
  - `dry_run` (bool).
  - `freed_size` (int, apparent bytes **actually freed**; 0 for dry-run).
  - `planned`: under dry-run, the list of items that "would have been executed".
  - `acted`: the list of items actually carried out (delete / trash).
  - `already_gone`: the list of items whose targets were already gone.
  - `skipped_stale`: the list of items skipped for staleness.
  - `of_outcome(code) -> 列表`: subset by outcome code; outcome codes include at least `"deleted"`, `"trashed"`,
    `"already-gone"`, `"skipped-stale"`, `"planned"`.
  - `outcome_counts() -> dict`: counts per outcome code.
  - **Each entry** in the lists above has `.action`, and `.action.item.path` is usable.

### High-level session: `SweepSession`
- `SweepSession(spec, policy, clock=None)`: `spec` is a `ScanSpec`, `policy` is a
  `RetentionPolicy`, `clock` is a zero-argument callable (see "Clock convention").
- Methods / attributes:
  - `scan()`: scan once and cache (the only disk-reading place); returns the list of `SweepItem`s.
  - `items`: the scanned list of `SweepItem`s (each element has attributes like `path`, `allocated_size`).
  - `plan() -> SweepPlan`: build a plan from the current snapshot. Call `scan()` explicitly first; support for implicit first-use scanning is optional. The cached snapshot is reused.
  - `preview() -> 运行报告`: **dry-run** the current plan (`dry_run=True`), modifying no files.
  - `apply(ignore_stale=False, trash_func=None, delete_func=None) -> 运行报告`: actually execute the current
    plan. `preview()` and the subsequent `apply()` operate on the **same snapshot / same plan**.

### Report rendering: `SweepReport`
- `SweepReport(plan, run=None, policy=None)`: wraps a plan (and optionally a run report and a policy).
- Methods:
  - `as_dict() -> dict`: contains at least the key `action_count`.
  - `as_json() -> str`: the JSON string of `as_dict()`.
  - `render_text() -> str`: a non-empty human-readable text summary.

### Layered configuration: `merge_documents` / `load_session`
- `merge_documents(*documents) -> dict`: **deep-merge** multiple config documents left to right:
  - later documents override earlier **scalars**;
  - **list-typed** keys (such as `roots`, `include_ext`, `rules`, etc.) are **append-merged** rather than replaced wholesale;
  - a key explicitly set to `None` means **delete** it.
  - Document structure convention: the top level has a `scan` section (mapping to `ScanSpec` fields, such as `roots`, `include_ext`...)
    and a `policy` section (with `action`, `keep_last`, `rules` (list of rule specs), etc.).
- `load_session(*documents, clock=None) -> SweepSession`: merge several config documents and build a
  ready-to-use session from them (scan scope from the `scan` section, rules / order / limits / action from the `policy` section).

### Exceptions
- `ProtectedPathError`: a protected target encountered at execution time (hard error; rejects the whole plan before touching disk).
- `StalePlanError`: a target is stale at execution time (unless `ignore_stale`).
- Configuration-class errors (nonexistent scan roots, invalid arguments, unknown rule `kind` / ordering names) must be distinguished from execution-class errors
  and detectable before touching disk; their exception class names end with `Error` (or are defined inside the `core.sweep` package).
