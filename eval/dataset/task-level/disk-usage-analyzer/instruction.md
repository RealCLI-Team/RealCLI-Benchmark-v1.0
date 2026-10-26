# Add a "disk usage analyzer" to dupeGuru's core

## Background

My disk is full again, but I have no idea where the space actually went.
`du -sh *` only gives me a coarse per-directory size — it can't show **which
file types** are eating space, how much is taken by **old files that haven't
been touched in ages**, and can't tell **real data** apart from a pile of
**temporary junk / caches / logs**. I normally use dupeGuru (the repo is
already at `/app`; the `core` package can be used directly via `import
core.fs`, `import core.directories`, etc.) to find duplicate files, and its
`core` already has a very usable **file model with chunked / partial / sampled
hashing**, plus the ability to **walk directory trees according to directory
states (normal / reference / excluded)**.

I want to build a **scriptable, offline-runnable, purely read-only** "disk
usage analyzer" on top of it: give it a few directories, and it aggregates the
usage of the whole directory tree **bottom-up**, telling me where the space is
piled up, what it's piled up with, how old it is, and how much of it is
actually junk or duplicates that could be cleaned. **It only measures and
reports; it never deletes or modifies any file.**

Please add a purely additive Python package **`core/diskscan/`** under
dupeGuru's **`core/`** implementing this analysis pipeline. It must **build on
dupeGuru's existing `core.fs` (the file model and its hashing) and
`core.directories` (directory walking, directory states)**, rather than
rewriting these low-level pieces from scratch. Export the key classes and
functions from the `core.diskscan` top level, so that `from core.diskscan
import ...` works directly.

> Note: this is **not** about adding features to the GUI, and it needs no
> graphical interface or background daemon. It is a pure piece of library code
> that can be driven directly by unit tests, with all filesystem operations
> landing on real temporary directories.

## What to deliver

### 1. Both kinds of "size" must be computed accurately

In disk analysis, "size" has two meanings; both must be distinguished and
reported separately:

- **Apparent size**: the file's logical length, i.e. the number `ls -l` shows.
- **Allocated size**: how many bytes the file actually occupies on the block
  device. Even a 1-byte file occupies a whole filesystem block; and a **sparse
  file** may actually occupy **less** than its apparent size. When I want to
  know "how much space would deleting this actually free", this is the honest
  number. Derive the allocated size from the file's block count (i.e. `stat`'s
  `st_blocks`, per the POSIX convention of 512 bytes per block); fall back to
  apparent size when that's unavailable.

Produce a record for each scanned file, snapshotting its **path, apparent
size, allocated size, modification time / access time**, etc., and be able to
compute the **slack** it wastes due to block alignment. At the start of an
analysis, capture the "current time" **exactly once** as the clock shared by
the whole analysis, so that all "how old is this file" computations are based
on the same baseline and are reproducible.

### 2. Scanning: directories → file records

Given a set of directories, produce a batch of file records. This layer must
**sit on top of `core.directories`**, so it naturally supports its directory
states:

- **Reference directories**: files in directories marked as "reference" are
  still **counted in the usage statistics**, but must be **flagged** (later
  stages like "cruft detection" must respect this flag and must not treat
  files in the master copies as junk).
- **Excluded directories**: excluded directories are not scanned at all.
- Also support filtering by **extension** (keep only / exclude certain
  suffixes), by filename **glob** patterns, and by **size range**.
- The order of scan results must be **deterministic** (it must not jitter with
  directory traversal order).
- Note: **empty directories** (directories with no files at all) must also be
  visible to later stages — they are themselves cleanable objects; don't lose
  them just because "the tree is built from files".

Scanning is the **only stage in the whole pipeline that actually reads the
disk**; all subsequent aggregation / breakdown must be based on this batch of
records already obtained, without re-reading the disk.

### 3. Usage tree: bottom-up aggregation

Aggregate the file records into a **usage tree** corresponding to the directory
hierarchy. Each directory node must be able to report:

- the usage of the files it **directly** contains, and the usage of its
  **entire subtree** (both kinds of size);
- the **file count** and **subdirectory count** in the subtree.

A parent's subtree usage must be **exactly equal** to the sum of its children's
subtree usages plus the usage of its own direct files — in other words, the
aggregation must be **bottom-up and reconcilable**. Based on this tree, you
should be able to give:

- **Hotspots**: the directories occupying the most space, ordered by allocated
  size **from large to small**, in a deterministic order;
- the **largest files**;
- the total slack wasted due to block alignment across the whole tree.

### 4. Breakdown by dimension

Besides "by directory", you should also be able to slice horizontally,
answering "**what** is occupying the space, and **how old** is it":

- **By extension**: group files by suffix, reporting per group the file count
  and the totals of both kinds of size; files without a suffix go into one
  group. Results ordered by usage **from large to small**.
- **By age**: bucket files by "how long since last modified" into an ordered
  set of **age buckets** (e.g. "within a day / within a week / within a month /
  within a quarter / within a year / older"). The age buckets must be
  **exhaustive** — every file lands in exactly one bucket, and the totals lose
  no files. The bucketing must be **customizable** by the caller (given a set
  of "label → upper bound in seconds" cut points, with the last bucket being
  the catch-all "older").

### 5. Cruft detection

Provide a **rule-based, pluggable** classifier that flags "things that look
like they can probably be cleaned up" (**labeling only, never deleting**),
covering at least these categories:

- **Empty files** (zero bytes);
- **temporary / system junk** (such as `*.tmp`, `*.swp`, `.DS_Store`,
  `Thumbs.db`, etc.);
- **build / dependency caches** (such as `__pycache__`, `node_modules`,
  `*.pyc`, `*.o`, etc., or files located inside such cache directories);
- **logs** (`*.log`);
- **backup copies** (such as `*.bak`, `foo~`, etc.).

A file may hit multiple rules; classification must give it one **primary
category** and record all the categories and reasons it hit. The overall cruft
report must be able to report: how many files were hit, how much space could
be freed in total, and **per-category** subtotals. In particular, **files in
reference (master-copy) directories never count as cruft by default** — since
the user declared them as master copies, even junk-looking files inside them
are ones they want to keep.

### 6. Duplicate waste

As a bonus, give an estimate of "how much space is wasted on copies with
**byte-identical content**". **Reuse the tiered hashing already in `core.fs`**
(bucket by size first, then compare cheap **partial** digests, and only compare
**full** digests for the survivors; very large files use **sampled** digests,
consistent with dupeGuru's trade-offs for big files) to group files with
identical content, and report per group: the size of a single copy, the member
count, and the **reclaimable bytes** (the total of all copies except the one
kept).

- This step **only quantifies; it does not decide who to delete, let alone
  delete anything** (that's the job of other tools).
- Files of different sizes can never be judged duplicates (they don't even
  need to be read); files of the same size but different content must not be
  misjudged as duplicates either.
- Zero-byte files count as "empty-file cruft", but **not** as "duplicate data".
- This step needs to read file contents for hashing, so it must be
  **switchable off**, to be skipped when the caller only wants a quick usage
  overview.

### 7. Thresholds and reporting

Provide a high-level entry point (a session class) that chains "scan → build
tree → breakdown → cruft / duplicates" together, making the common flow
one-shot: a single `analyze()` yields a complete, **serializable** analysis
result, which is **cached**. The whole session **makes no file changes**.

The analysis result must be inspectable and exportable:

- It can be converted wholesale to a **dict** and to **JSON**, and also
  rendered into a human-readable **text summary** (total usage, hotspots,
  tables by extension / by age, cruft and duplicate subtotals, etc.).
- Provide a set of **threshold rules**: the caller declares several limits
  (e.g. "per-directory usage limit", "cruft fraction limit", "duplicate
  fraction limit", "empty-file count limit", etc.), evaluates them against the
  analysis result, produces a set of **breaches** with **severity levels**
  (info / warning / critical), and gives an overall pass/fail judgment (based
  on the most severe breach).

### 8. Layered configuration

Real inspection tasks are often written in **layered** configuration: a base,
overlaid with several overlays (differences per machine / user). Provide an
entry point that **deep-merges** multiple configuration documents into one and
constructs a session from it (with scan scope, age-bucket customization,
threshold rules, etc.):

- Later documents override earlier ones' **scalars**; for **list-type** fields
  (such as `roots`, `include_ext`, etc.) **append-merge** rather than replacing
  wholesale; a key explicitly set to `null` means **delete** it.

## Constraints

- Only **add** files under `core/diskscan/`; **do not modify** any existing
  dupeGuru code. Judging will be based on the repo's original dupeGuru source.
- You must **reuse** `core.fs` and `core.directories`; do not bypass them and
  write your own file traversal / hashing.
- Pure Python, following the repo's existing code style. Have a clear exception
  hierarchy, distinguishing **configuration errors** (nonexistent scan roots,
  invalid parameters — these should be detectable before touching the disk)
  from everything else.
- The whole analysis must be drivable directly by unit tests in an environment
  with **no graphical interface / daemon whatsoever**, purely offline, using
  real temporary directories, and must be **read-only throughout, never
  modifying the filesystem**.

## How it will be verified

We will lay out various file layouts in real temporary directories (controlled
sizes, modification times, sparse files, duplicate content, all kinds of junk,
empty directories, etc.), call your exported interfaces directly, and check
their **observable behavior**: whether apparent size and block-based allocated
size are each computed correctly, whether the usage tree reconciles bottom-up,
whether hotspot and largest-file ordering is correct and deterministic, whether
the by-extension / by-age breakdowns are exhaustive and computed correctly
(including custom age buckets), whether each kind of cruft is correctly
identified and reference directories are exempted, whether duplicate waste is
counted only when content is truly identical (the size pre-filter produces no
false groups, empty files don't count as duplicates), whether empty directories
are found, whether the analysis is read-only throughout, whether the result can
be serialized, whether threshold rules are judged correctly by severity, and
whether layered configuration merges and materializes correctly. Scoring only
cares about these external behaviors, not how you organize the implementation
internally.

## Public API contract (scoring depends on it)

The scoring script **does not look at your internals**, but it will `import
core.diskscan` **using exactly the names below**, read attributes, call
methods, and pass keyword arguments. **Symbol names, import paths, method
signatures, and semantics must match this section exactly** (how you split
modules internally and name private functions is up to you). Everything must be
available directly from the package top level: `from core.diskscan import <名字>`,
or `import core.diskscan as D` followed by `D.<名字>`.

Internal organization is up to you, but all the names below must be exported at
the top level of `core/diskscan/__init__.py`.

### 1. Required top-level exports (any missing one means a score of 0)

These names (classes / functions / constants) must exist at the top level:

- `ScanSpec`, `DiskScanSession`, `FileEntry`, `UsageTree`, `AnalysisReport`
- `breakdown_by_extension`, `breakdown_by_age`, `breakdown_by_top_directory`
- `CruftClassifier`, `find_duplicate_sets`
- `load_session`, `load_settings`, `load_thresholds`, `merge_documents`
- `allocated_size_of`
- `default_thresholds`, `ThresholdSet`, `ThresholdRule`
- Cruft category constants: `CATEGORY_EMPTY`, `CATEGORY_TEMP`, `CATEGORY_CACHE`, `CATEGORY_LOG`, `CATEGORY_BACKUP`
- Severity constants: `SEVERITY_INFO`, `SEVERITY_WARNING`, `SEVERITY_CRITICAL`
- Exceptions: `DiskScanConfigError`, `InvalidScanRootError` (the latter is a subclass of the former)

### 2. `allocated_size_of(stat_result) -> int`

Given an `os.stat_result`, return its **allocated size** in bytes. Computed as
`st_blocks * 512` (the POSIX 512-bytes-per-block convention); when there is no
`st_blocks`, fall back to `st_size`. Scoring uses it to compute the expected
value and then compares byte-for-byte with `FileEntry.allocated_size`, so the
two must **share the same source and algorithm**.

### 3. `ScanSpec`

`ScanSpec(roots, reference_roots=None, excluded_roots=None, include_ext=None,
exclude_ext=None, include_globs=None, exclude_globs=None, min_size=0, max_size=0, ...)`

- Positional/keyword argument `roots`: list of scan roots (either `str` or
  `pathlib.Path` is fine).
- `reference_roots`: list of reference (master-copy) directories; files inside
  them still count toward usage, but the corresponding `FileEntry.is_reference`
  is `True`, and by default they are never judged as cruft.
- `excluded_roots`: list of directories excluded entirely from scanning.
- `include_ext` / `exclude_ext`: keep only / exclude by extension (no leading
  dot, case-insensitive).
- Must support construction from a dict (`load_session` uses this); extension
  comparison is case-insensitive and strips the leading dot.

### 4. `FileEntry` (single-file record)

One per scanned file. Scoring reads the following **read-only attributes**:

- `path`: `pathlib.Path`, absolute path, `==`-equal to the path at write time.
- `name`: the file name (`path.name`).
- `apparent_size`: apparent size = `st_size`.
- `allocated_size`: allocated size = `allocated_size_of(stat)`.
- `slack_bytes`: block-alignment slack = `max(0, allocated_size - apparent_size)`; sparse files clamp to 0.
- `is_sparse`: `True` when `allocated_size < apparent_size`.
- `is_empty`: `True` for zero-byte files.
- `is_reference`: `True` when located inside a reference root.
- Must be able to compute "how long since last modified"; scoring drives age
  bucketing through the session/report's unified `ref_time` baseline.

### 5. `DiskScanSession` (high-level entry point)

`DiskScanSession(spec, clock=None, age_ladder=None, cruft_rules=None,
detect_duplicates=True, duplicate_min_size=1, ...)`

- The first positional argument is a `ScanSpec`. Scoring will pass
  `clock=lambda: NOW` (an injectable clock returning float seconds); the whole
  analysis shares this single "current time".
- `analyze(force=False, ref_time=None, j=...) -> AnalysisReport`: scans and builds the report, **caching** the result;
  `force=True` rescans; `ref_time` overrides the baseline clock for this analysis (scoring uses it to pin ages). Read-only throughout, never modifying the filesystem.
- `evaluate(thresholds, force=False) -> ` (threshold result object, see section 11): evaluates a set of thresholds against the report;
  `thresholds` may be a `ThresholdSet` or a bare iterable of rules.
- When a nonexistent scan root is passed in, `analyze()` must raise `InvalidScanRootError`
  (or its parent class `DiskScanConfigError`) **before touching the disk**.

### 6. `AnalysisReport` (analysis result)

The return value of `session.analyze()`. Scoring reads:

- Aggregate scalars: `total_files`, `total_apparent`, `total_allocated` (the latter always ≥ the former).
- `entries`: an iterable sequence of all `FileEntry` objects, in **deterministic** order (sorted ascending by path string; identical across two analyses).
- `tree`: a `UsageTree` (see section 7).
- `hotspots(limit=N) -> [UsageNode, ...]`: the directories with the highest usage, ordered by `total_allocated`
  **from large to small**, in a deterministic order. **Candidates include only subdirectory nodes; the scan root node itself does not count as a hotspot** (otherwise the root would always
  be the largest, which is meaningless); sorting uses `total_allocated` descending as the primary key and the node path string ascending as
  the stable secondary key.
- `largest_files(limit=N) -> [FileEntry, ...]`: the largest files, ordered by `allocated_size` from large to small.
- `extension_breakdown`: the by-extension `Breakdown` (see section 8).
- `age_breakdown`: the by-age `Breakdown`.
- `cruft`: a `CruftReport` (see section 9).
- `duplicates`: a `DuplicateReport` (see section 10).
- `as_dict() -> dict`: contains at least the top-level keys `"totals"` and `"tree"`; `d["totals"]["files"]` is the total file count.
- `as_json() -> str`: after `json.loads`, `parsed["totals"]["files"]` equals the file count.
- `render_text() -> str`: a non-empty human-readable text summary.

### 7. `UsageTree` / `UsageNode` (usage tree)

`UsageTree`:

- `node_at(path) -> UsageNode | None`: locate a node by path.
- `empty_dirs() -> [UsageNode, ...]`: directory nodes whose entire subtree contains no files (including nested empty directories);
  scoring checks via the nodes' `name` (e.g. `"empty_one"` and `"nested_empty"` are included, the non-empty directory `"has"` is not).

`UsageNode` (each directory node):

- `path`, `name`.
- `total_apparent`, `total_allocated`: the two sizes of the **entire subtree** (its own direct files + all child subtrees).
- `total_files`: file count within the subtree.
- `direct_allocated`: the allocated size of files **directly** in this directory (excluding subdirectories).
- Reconciliation invariant: parent `total_allocated` == sum of children `total_allocated` + parent `direct_allocated`;
  same for `total_apparent`.

### 8. Breakdown tables (by extension / by age / by top-level directory)

`breakdown_by_extension(entries)`, `breakdown_by_age(entries, ref_time, ladder=None)`,
and `breakdown_by_top_directory(tree, depth=1)` all return the same kind of **Breakdown** object. It can also be obtained from the report's
`extension_breakdown` / `age_breakdown`. It must support:

- **Iterable**: `for row in bd` yields row objects one by one; a row object has attributes `key`, `count`, `apparent`, `allocated`.
- `row_for(key) -> row | None`: look up a row by key (extensions without dot, lowercase; files without an extension go into one unified group).
- `rows`: list of rows (`bd.rows[0]` is the heaviest row).
- `total_count`, `total_apparent`, `total_allocated`: totals of each column.
- `top(N) -> Breakdown`: keep only the heaviest N rows; the rest are **folded** into a catch-all row whose key is `"(other)"`,
  such that `top(N).total_allocated == 原.total_allocated` (truncation loses no bytes).
- **Ordering**: the by-extension / by-top-level-directory tables are ordered by `allocated` **from large to small**; the by-age table is output in **age-bucket order**
  (youngest→oldest).
- The age buckets must be **exhaustive**: every file lands in exactly one bucket, and the totals lose no files; the buckets can be customized by the caller
  (see section 12, `age_buckets`). The default age-bucket keys include at least `"day"` and `"older"` (the catch-all).

### 9. Cruft detection: `CruftClassifier` and `CruftReport`

`CruftClassifier(rules=None, include_reference=False)`; the report's `cruft` attribute is a
**CruftReport**:

- **Iterable**: `for tag in report.cruft` yields "hit tag" objects; a tag has `.entry` (the corresponding `FileEntry`).
- `paths() -> [Path, ...]`: paths of all files judged as cruft.
- `category_totals() -> dict`: `{类别常量: (count, allocated)}`, keyed by primary category.
- Category constants: `CATEGORY_EMPTY` (zero bytes), `CATEGORY_TEMP` (`*.tmp`/`*.swp`/`.DS_Store`/`Thumbs.db`, etc.),
  `CATEGORY_CACHE` (`__pycache__`/`node_modules`/`*.pyc`/`*.o`, etc., or located inside such cache directories),
  `CATEGORY_LOG` (`*.log`), `CATEGORY_BACKUP` (`*.bak`/`foo~`, etc.).
- A file may hit multiple rules; give it one **primary category**; the allocated total of a category in `category_totals()`
  must equal the sum of `.entry.allocated_size` over the hit tags under that category.
- **Files inside reference directories never count as cruft by default** (`include_reference=False`).

### 10. Duplicate waste: `find_duplicate_sets` and `DuplicateReport`

`find_duplicate_sets(entries, min_size=1, big_file_size=...) -> DuplicateReport`; the report's
`duplicates` attribute is exactly this. It must **reuse the tiered hashing of `core.fs`** (bucket by size first → partial digests → full/sampled digests).

**DuplicateReport**:

- `group_count`: number of groups with identical content.
- `duplicate_count`: number of redundant copies (per group, the total excluding the one kept).
- `wasted_allocated`: total reclaimable allocated size.
- `sets`: list of `DuplicateSet`.

**DuplicateSet**:

- `count`: member count of the group.
- `wasted_apparent`: reclaimable apparent bytes = `(count-1) * 单份表观大小`.
- (`wasted_allocated` likewise, using allocated size.)

Hard semantic constraints: files of different sizes are **never** in the same group (they aren't even read); files of the same size but different content are **not** in the same group;
**zero-byte files never count as duplicates** (they belong to empty-file cruft).

### 11. Thresholds and reporting

`default_thresholds(max_total_allocated=None, max_hotspot_allocated=None,
max_cruft_fraction=None, max_duplicate_fraction=None, max_empty_files=None) -> ThresholdSet`

- Only turns the limits you pass into rules. Scoring will use keywords like `max_empty_files=`, `max_duplicate_fraction=`,
  `max_total_allocated=`.
- The names of the resulting "breaches" must correspond to the limit keys: the empty-file breach is named `"empty-files"`, the duplicate-fraction breach is named `"duplicate-fraction"`.

`ThresholdRule(name, metric, limit, severity=SEVERITY_WARNING, direction="above", ...)`

- `name`: positional argument (breaches are named after it).
- `metric`: `callable(report) -> number`, taking the observed value from the report.
- `limit`: the threshold limit.
- `severity`: one of the severity constants (`SEVERITY_INFO` / `SEVERITY_WARNING` / `SEVERITY_CRITICAL`).

`ThresholdSet(rules)`: a collection of rules.

`session.evaluate(thresholds)` returns a **threshold result object**, which must support:

- `passed`: a bool; `True` when there are no breaches of `warning` level or above (`info` level doesn't count as failure).
- **Iterable**: `for breach in result` yields breaches; a breach has `.name`, `.severity`.
- `breaches`: list of breaches, **sorted by severity from most to least severe** (`breaches[0]` is the most severe).
- `worst_severity`: the highest severity triggered (`None` when clean).

Severity ordering: `SEVERITY_INFO` < `SEVERITY_WARNING` < `SEVERITY_CRITICAL`.

### 12. Layered configuration

`merge_documents(documents) -> dict`: **deep-merge** a list of config dicts into one:

- later documents override earlier ones' **scalars**;
- **list-type** fields (`roots`, `include_ext`, `exclude_ext`, `reference_roots`, `excluded_roots`, etc.)
  are **append-merged** (deduplicated) rather than replaced wholesale;
- a key explicitly set to `None` means **delete** it (the merged result does not contain that key).

`load_settings(documents) -> dict`: merge `documents` on top of the default settings, returning a settings dict.

`load_thresholds(settings) -> ThresholdSet | None`: construct a threshold set from the `thresholds` block in the settings
(e.g. `{"max_empty_files": 0}`); return `None` when that block is absent.

`load_session(documents) -> DiskScanSession`: materialize layered configuration into a session that can directly `analyze(ref_time=...)`.
The recognized config keys include at least:

- `roots`: list of scan roots (when missing and there is no `paths`, a configuration error should be raised).
- `include_ext` / `exclude_ext`: extension filtering.
- `detect_duplicates`: bool, whether to do duplicate detection (can be turned off when only a quick usage overview is wanted).
- `age_buckets`: custom age buckets, of the form `[["recent", 秒数], ..., ["older", None]]`
  (the last upper bound being `None` marks the catch-all bucket); after materialization, the row `key` order of `report.age_breakdown` is exactly these labels.
- `thresholds`: threshold block (used by `load_thresholds`).
