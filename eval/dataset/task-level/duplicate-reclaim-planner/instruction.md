# Add a "duplicate-file reclaim planner" to dupeGuru's core

## Background

Several of my disks are chronically stuffed with duplicate files: the same
photos scattered across multiple directories, a download directory full of
`报告 (1).pdf`、`报告 copy.pdf`, and backup scripts copying them back and
forth. I've long used dupeGuru (the repo is already at `/app`; the `core`
package can be used directly via `import core.fs`, `import core.directories`,
etc.) to find duplicates, but its GUI leans toward "manually ticking files to
delete one by one". What I want is something **scriptable, previewable, and
safely executable**: give it a few directories, and it groups files with
**byte-identical content** on its own, picks **which one to keep per group**
according to rules I set, and first gives me a **read-only reclaim plan** so I
can clearly see "which files will be deleted, how much space will be freed";
only after I've confirmed it does it actually reclaim.

Please add a purely additive Python package **`core/reclaim/`** under
dupeGuru's **`core/`**, implementing this "scan → group → plan → execute"
pipeline. It must **build on dupeGuru's existing `core.fs` (the file model and
its chunked / partial / sampled hashing) and `core.directories` (directory
walking, distinguishing normal / reference / excluded by directory state)**,
rather than rewriting these low-level pieces from scratch. Export the key
classes and functions from the `core.reclaim` top level, so that `from
core.reclaim import ...` works directly.

> Note: this is **not** about adding features to the GUI, and it needs no
> graphical interface or background daemon. It is a pure piece of library code
> that can be driven directly by unit tests, with all filesystem operations
> landing on real temporary directories.

## What to deliver

### 1. Content identity

Whether two files "are the same thing" should be judged by **content**, not by
file name or path. Reuse the hashing capabilities already in `core.fs` (it
already computes whole-file digests, cheap **partial** prefix digests, and
**three-point sampled** digests for very large files), and wrap a clear
"content identity" concept on top of it:

- Provide a content-identity object that is comparable and usable as a dict
  key, plus an entry point to compute it from a `core.fs.File`.
- Comparison must be **tiered**, from cheap to expensive: first compare
  **size** (different sizes can never be identical — they don't even need to
  be read), then compare **partial digests** (reading only a small prefix,
  which filters out large numbers of files early), and only compare **full
  digests** for the survivors (very large files use sampled digests, consistent
  with dupeGuru's trade-offs for big files).
- Support at least three strengths: **size only**, **up to partial digest**,
  **up to full content**. Identical content must have equal identities at the
  same strength; different content must not.

### 2. Scanning: directories → candidate files

Given a set of directories, produce a batch of "candidate files". This layer
must **sit on top of `core.directories`**, so it naturally supports its
directory states:

- **Reference directories**: files in directories marked as "reference" are
  **protected** — they participate in comparison and can serve as a group's
  keeper, but **will never be reclaimed**. This is how the user expresses
  "these are my master copies; use them for dedup, but absolutely don't touch
  them".
- **Excluded directories**: excluded directories are not scanned at all.
- Also support filtering by **extension** (keep only / exclude certain
  suffixes), by filename **glob** patterns, and by **size range**, so that
  photo dedup doesn't waste time reading a pile of videos.
- The order of scan results must be **deterministic** (it must not jitter with
  directory traversal order).

### 3. Grouping: cluster files with identical content

Cluster the candidate files by "content identity" into **duplicate groups**,
each group being two or more files that are byte-identical to each other
(equivalent in spirit to dupeGuru's own content-based scan, but producing
**explicit groups** rather than pairwise matches, since reclaiming works at
the granularity of "keep one in a group, delete the rest").

- Grouping must reuse the tiered comparison above, minimizing I/O: bucket by
  size first, subdivide within buckets by digests; singletons (those with no
  companions) must be eliminated early, with no unnecessary full reads.
- Each group must be able to report: the size of a single copy, the member
  count, and the **reclaimable bytes** (the total size of all copies except
  the keeper; if the whole group is protected, reclaimable is 0).
- Files that are too small (below a very small default threshold, e.g. empty
  files) are ignored by default — reclaiming a few bytes isn't worth it, and
  small-file contents collide easily; but this threshold must be adjustable by
  the caller (including down to 0).
- The output order of groups must be **deterministic**, with groups that **can
  free more space** listed first.

### 4. Keeper strategy

For a group of files with identical content, **which one to keep and which to
reclaim** is a question of strategy. Make it a pluggable, composable strategy
family, including at least:

- keep the **oldest** (treated as the original) / keep the **newest**;
- keep the one with the **shortest path** (the one closest to the root is
  usually the "real one"; deeply buried ones are often scattered copies) /
  keep the one with the longest path;
- keep the one whose **name looks least like a copy** (`photo.jpg` beats
  `photo (1).jpg` / `photo copy.jpg`);
- keep the one located under some **preferred directory** (e.g. an
  `Originals/` folder).

A strategy should be expressed as an "ordering" — after sorting a group of
candidates, the first one is the keeper. Strategies must be **composable**:
sort by preferred directory first, break ties by secondary rules like "cleanest
name", and the whole process must be **deterministic** for the same input.
Additionally, **protected (reference) files** are always kept preferentially
and never become reclaim targets; a strategy only chooses "among the
reclaimable members" or "within groups that have no protected members".
Provide a factory entry point to build strategies from a **name / combination
spec**, raising an error for unrecognized strategy names.

### 5. The reclaim plan — pure and previewable

The planning step must be **purely functional**: it only observes and computes,
and **never modifies the filesystem**. For each group, after the keeper is
chosen, generate a **reclaim action** for each of the other copies; an action
supports at least four dispositions:

- **Delete** (unlink directly);
- **Move to trash** (recoverable);
- **Hardlink** to the keeper (delete the copy, then hardlink that directory
  entry to the keeper's inode — space is reclaimed but the path still reaches
  the content);
- **Symlink** to the keeper.

The plan object must be inspectable as a whole: how many keepers / how many
copies to reclaim in total, how many bytes can be freed overall, how many
actions of each type, and be able to export a **summary** convenient for
printing or converting to JSON. Keeping the plan as **data** (rather than
deleting as you compute) is exactly what makes it previewable, comparable, and
partially executable.

### 6. The executor — landing it safely

Only when the plan is handed to the executor does it actually touch the disk.
The executor must cover these safety semantics:

- **Dry-run**: perform all the checks (the items below) that a real execution
  would do, producing a result object of the same structure, but **skip the
  actual changes**. So a clean dry-run strongly predicts that a real run will
  also succeed.
- **Staleness protection**: the plan was computed against a snapshot taken at
  planning time. Before executing, **re-verify** that each file to be
  reclaimed still matches the snapshot (size / mtime unchanged, and still
  exists); if it has been modified or has disappeared, **refuse to execute and
  raise an error** by default, unless the caller explicitly asks to "ignore
  staleness", in which case it is recorded as **skipped** rather than crashing
  the whole run.
- **Protection check**: protected files must never become reclaim targets —
  normally planned plans contain no such actions, so when one is encountered
  (e.g. someone hand-crafted a bad action list pointing at a protected file),
  it must be raised as a **hard error**.
- **Idempotence**: running an already-executed plan again must be a **no-op**;
  a file to be reclaimed that "is already gone" because a previous round
  deleted it should be treated as **already reclaimed**, not as an error.
- Trash actions must be performed through an **injectable, replaceable
  callable** (tests will inject a fake one; never actually touch the system
  trash).
- The execution result must be able to report: what was done, what was skipped
  (and why), what failed, and the **bytes actually reclaimed**.

### 7. Session and layered configuration

Provide a high-level entry point (a session class) that chains "scan → group →
plan → execute" together, making the common flow one-shot: first `analyze()`
yields a read-only analysis result (including the plan) for preview, then
`apply()` lands it — and `apply()` must use exactly the plan that was just
previewed: never preview one plan and execute another.

Real cleanup tasks are often written in **layered** configuration: a base,
overlaid with several overlays (differences per machine / user). Provide an
entry point that **deep-merges** multiple configuration documents into one and
constructs a session from it:

- Later documents override earlier ones' **scalars**; for **list-type** fields
  (such as `roots`, `include_ext`, etc.) **append-merge** rather than
  replacing wholesale; a key explicitly set to `null` means **delete** it.

## Constraints

- Only **add** files under `core/reclaim/`; **do not modify** any existing
  dupeGuru code. Judging will be based on the repo's original dupeGuru source.
- You must **reuse** `core.fs` and `core.directories`; do not bypass them and
  write your own file traversal / hashing.
- Pure Python, following the repo's existing code style. Have a clear exception
  hierarchy, distinguishing **configuration errors** (unknown strategies,
  nonexistent scan roots, invalid parameters — these should be detectable
  before touching the disk) from **execution-time errors** (staleness,
  protection, action failures).
- The whole pipeline must be drivable directly by unit tests in an environment
  with **no graphical interface / daemon whatsoever**, purely offline, using
  real temporary directories.

## How it will be verified

We will lay out various duplicate-file layouts in real temporary directories,
call your exported interfaces directly, and check their **observable
behavior**: files with identical content are grouped correctly, reclaimable
bytes are computed correctly, the various keeper strategies (including
combination, preferred directories, and "cleanest name") pick the right
keepers, reference-directory files are never reclaimed, planning purity
(planning / previewing doesn't touch the disk), the landing effects of the
four action types (delete / trash / hardlink / symlink), dry-run makes no
changes, idempotence, staleness protection (refusing to execute after a file
to be reclaimed is modified, skipping instead when staleness is ignored), hard
errors for protected files, the size pre-filter producing no false groups,
extension / excluded-directory filtering, the hashing semantics of content
identity, and the merging and materialization of layered configuration.
Scoring only cares about these external behaviors, not how you organize the
implementation internally.

## Public API contract (scoring depends on it)

The scoring script accesses the following names **only** through `import
core.reclaim as R` (the package is at `/app`, i.e. `core/reclaim/`), and
asserts their observable behavior. Export these symbols **from the
`core.reclaim` top level** (`from core.reclaim import ...` must work
directly); names, spelling, signatures, and semantics must match the text
below. How you split modules internally is unrestricted, but **this contract
of top-level exports must not be renamed**.

### Action type constants (reclaim dispositions)

The `core.reclaim` top level must export four **action kind** constants (their
values don't matter; scoring accesses them by name and passes them through
whole):

- `DELETE` — unlink directly.
- `TRASH` — move to trash (via an injectable callable).
- `HARDLINK` — delete the copy, then hardlink to the keeper.
- `SYMLINK` — delete the copy, then symlink to the keeper.

### Comparison strength constants (content-identity strength)

The top level must export at least these two strength constants (there may
also be `PARTIAL`):

- `SIZE_ONLY` — judge sameness by size only (coarse mode).
- `FULL` — compare up to full content (sampled digests for big files).

### `ScanSpec`

Scan specification class; constructor signature (keyword parameter names must
match):

```
ScanSpec(roots, reference_roots=None, excluded_roots=None,
         include_ext=None, exclude_ext=None,
         include_globs=None, exclude_globs=None,
         min_size=0, max_size=0)
```

- `roots`: list of directories to scan (may be `str` or `pathlib.Path`).
- `reference_roots`: list of reference (protected) directories; files inside
  them have `protected=True` and are never reclaimed.
- `excluded_roots`: list of directories excluded entirely, not scanned.
- `include_ext`: keep only these extensions (lowercase, no dot, e.g.
  `["jpg"]`).

### `CandidateScanner`

- `CandidateScanner(spec)` — takes a `ScanSpec`.
- `.scan()` — returns a list of candidates (`Candidate`) in deterministic
  order. Raises `InvalidScanRootError` when a scan root does not exist.

### `Candidate` and factory

- `candidate_from_path(path, protected=False)` — construct a candidate object
  from a path.
- A candidate object must expose: `.path` (`pathlib.Path`), `.file` (the
  underlying `core.fs.File`), `.identity()` (returns a content-identity object
  that is comparable and usable as a dict key; equal for same content, unequal
  for different content).
- `same_content(file_a, file_b)` — a top-level function taking two
  `core.fs.File` objects, returning `True` for same content and `False` for
  different content.

### Grouping

- `group_identical(candidates, ...)` — a top-level function that clusters
  candidates into duplicate groups by content identity.

### Keeper strategies

- `make_strategy(spec)` — builds a strategy from a **name / combination
  spec**; raises `UnknownStrategyError` for unrecognized names.
- Strategy names (strings; spelling must match exactly): `"oldest"`,
  `"newest"`, `"shortest_path"`, `"longest_path"`, `"cleanest_name"`,
  `"biggest"`, plus the parameterized `"preferred_dir"`.
- **Combination strategies**: putting the above items into a list is a
  combination; earlier ones take priority, later ones break ties.
  `"preferred_dir"` is given as a two-tuple `("preferred_dir", [目录路径, ...])`. For example `strategy=[("preferred_dir", [pref]),
  "cleanest_name"]`.

### Planning: `ReclaimPlanner` / `ReclaimAction` / `ReclaimPlan`

- `ReclaimPlanner(strategy="oldest", action=DELETE)` — a planner that builds a
  plan from duplicate groups.
- `ReclaimAction(kind, victim, keeper)` — a reclaim action; `kind` is one of
  the action constants above, and `victim` / `keeper` are candidate objects.
  It must expose `.reclaimed_bytes` (the bytes this action can free).
- `ReclaimPlan(actions, keepers, strategy_name="")` — the plan object; it must
  expose:
  - `.actions` — the list of actions, sorted with **groups that can reclaim
    more first** (`actions[0].reclaimed_bytes` is the single-copy size of the
    largest group).
  - `.keepers` — the list of per-group keepers (candidate objects, with
    `.path` / `.path.name`).
  - `.victim_count` — the number of copies to be reclaimed.

### Execution: `ReclaimExecutor`

- `ReclaimExecutor()` — the executor; `.execute(plan)` executes a
  `ReclaimPlan` and returns a result report object.
- An action targeting a **protected** file is a hard error, raising
  `ProtectedPathError` (and not touching that file).

### Session: `ReclaimSession`

Constructor signature (keyword parameter names must match):

```
ReclaimSession(spec, strategy="oldest", action=DELETE,
               min_size=None, strength=FULL, big_file_size=...)
```

Methods and returns:

- `.analyze()` — **purely functional** (doesn't touch the disk); returns an
  analysis result object that must expose:
  - `.group_count` — number of duplicate groups.
  - `.duplicate_count` — number of redundant copies (the sum of each group's
    member count minus one).
  - `.reclaimable_bytes` — total reclaimable bytes (protected members don't
    count).
  - `.plan` — the `ReclaimPlan` above.
- `.preview()` — a dry-run, doesn't touch the disk.
- `.apply(dry_run=False, ignore_stale=False, keep_going=False, trash_func=None)` — executes for real,
  using exactly the plan from `analyze()`; returns an execution report object.
  - `trash_func`: the injectable callable `func(path)` invoked by `TRASH`
    actions (tests inject a fake; it is called once with the copy's path).
  - When a file to be reclaimed is modified / disappears after planning, raise
    `StalePlanError` by default; with `ignore_stale=True` it is recorded as
    skipped instead of crashing.

The execution report object must expose: `.dry_run` (bool), `.done_count`
(actual/preview completion count), `.skipped_count` (skip count).

### Layered configuration loading

- `load_settings(documents)` — deep-merge a series of config documents into
  one settings dict:
  - **scalars**: later overrides earlier;
  - **list-type** fields (such as `roots`, `include_ext`, etc.) are
    **append-merged**;
  - a key explicitly set to `None` means **delete** that key (the merged
    result does not contain it).
- `load_session(documents)` — construct a usable `ReclaimSession` from layered
  config documents; the config may contain keys such as `roots` / `strategy` /
  `action` (`action` takes one of the action constants above).

### Exception hierarchy

The top level must export the following exceptions (they may share a common
base class, but these names must exist with the semantics above):

- `UnknownStrategyError` — unknown strategy name (configuration error,
  detectable before touching the disk).
- `InvalidScanRootError` — scan root doesn't exist / is not a directory
  (configuration error).
- `StalePlanError` — the plan is stale relative to the disk (execution-time
  error).
- `ProtectedPathError` — an action points at a protected file (execution-time
  error).

### Report submodule

`core.reclaim` must expose the submodule `report` (`R.report`), containing at
least:

- `report.as_dict(analysis)` — serialize the result of `analyze()` to a dict;
  it must contain `d["plan"]["victim_count"]` and
  `d["plan"]["reclaimable_bytes"]`.
- `report.render_text(analysis)` — return a printable string summary
  containing the text `Reclaimable`. The reporting process is pure and doesn't
  touch the disk.
