Our team uses PDM to manage dependencies, and one of the biggest headaches in collaboration is `pdm.lock` version conflicts: after two people run `pdm add` / `pdm update` on different branches, a git merge inevitably conflicts on the lockfile, and manually aligning the pinned versions, hashes, and grouping info of hundreds of packages is practically infeasible — the only option is to delete one side and re-run `pdm lock`, which is slow and easily introduces unexpected version drift.

I want PDM to natively provide a three-way lockfile merge capability. The repo is at `/app` (PDM's own source, already installed in editable mode, so the `pdm` command is directly usable). Implement a new command:

```bash
pdm merge-lock BASE OURS THEIRS [-o OUTPUT] [--on-conflict STRATEGY] [--dry-run] [--json]
```

The design goal is that it can be configured directly as a git merge driver (`driver = pdm merge-lock %O %A %B`), so its inputs are three file paths: the common ancestor (base), our version (ours, i.e. the working copy), and their version (theirs, i.e. the changes being merged in). The merge result **overwrites ours** by default; with `-o/--output` it writes to the given path (auto-creating parent directories if they don't exist). The whole merge must not access the network and must not re-resolve dependencies — it's purely a three-way merge over the structure of the three lockfiles.

The merge semantics follow git three-way merge intuition:

- A package's identity is determined by its requirement identity (package name, extras, environment markers, source URL, etc.); **the pinned version is not part of the identity**, so one branch bumping a version and another branch changing something else can merge correctly.
- Packages added on only one side are kept; additions with identical content on both sides merge cleanly; deleted on one side and untouched on the other → deleted; changed on both sides but identically → clean.
- When both sides changed the same package in different ways, merge field by field where possible: set-like fields such as `groups`, `dependencies`, and the file-hash list take a three-way set union (an entry deleted on one side and untouched on the other counts as deleted); single-value fields such as `version`, `requires_python`, `summary`, `marker` take the changed side as long as the other side didn't touch them.
- Everything else is a conflict, including: both sides bumping the same package to different versions, one side deleting a package while the other modifies it, both sides adding a same-named package with different content, both sides giving different hashes for the same file name, and the two sides changing a package's marker structure (multiple pins split by marker) differently.

Conflict handling is controlled by `--on-conflict`, default `fail`: print each conflicting package, the reason, and the three-way state to stderr, exit with a non-zero code, and **don't write a single byte** (don't touch ours, don't touch the `-o` target). The other three strategies decide automatically: `ours`/`theirs` take the entire entry from the corresponding side (including deletion); `newer` takes the side with the higher pinned version, a modification always beats a deletion, and on a version tie or when versions can't be compared, take theirs. Conflicts resolved by a strategy must be reported in the output with their count and which packages were involved.

The `[metadata]` table must be merged too: `groups` and `targets` merge by the three-way set rules; `lock_version` takes the higher of the two sides; `content_hash` follows the working copy (ours); for lock strategy flags (`cross_platform`, `static_urls`, etc.), if only one side changed them, take the changed side, and **if the two sides changed them in different directions, reject the merge with an error** (this case can't be merged safely — tell the user to align the strategy and re-lock first). Legacy boolean keys like `cross_platform = true/false` must not be carried into the output.

Two options for automation: `--dry-run` only checks the merge, prints a summary, writes no files, and exits non-zero when there are conflicts; `--json` prints a machine-readable JSON report to stdout (whether the merge is clean, total package count, lists of added/removed/updated packages, and details of each conflict including three-way state) — in this mode human-readable messages move to stderr, so stdout can be piped directly to `jq`. The report uses these exact key names:

```json
{
  "clean": true,
  "packages": 4,
  "changes": {"added": ["gamma 2.0.0"], "removed": ["legacy 0.9.1"], "updated": ["alpha 1.5.0"]},
  "conflicts": [{"package": "alpha", "field": "version", "base": {...}, "ours": {...}, "theirs": {...}}]
}
```

Entries under `changes.added` / `changes.removed` / `changes.updated` are `"<name> <version>"` strings (e.g. `"gamma 2.0.0"`), and each conflict entry includes a `"package"` key.

The output file itself must follow PDM lockfile conventions: keep the "@generated by PDM" comment header at the top, all `[metadata]` fields present, `[[package]]` entries sorted by package name, byte-identical output when run twice on the same inputs, and the output must load correctly through PDM's own lockfile reading logic.

Input validation is also required: missing files, TOML parse failures, non-PDM-format lockfiles (e.g. feeding in a `pylock.toml` must clearly state that only the PDM format is supported), or a lock version newer than what the current PDM can read — all must exit non-zero with clear error messages. Duplicate package entries (completely identical identity) within a single lockfile mean the file is corrupted and must be reported as an error rather than silently picking one.

When implementing, follow the repo's existing code organization and conventions (how commands are registered, how the lockfile format is read/written, how terminal output goes through the unified UI), and don't break the repo's own test suite. In the docs (`docs/usage/lockfile.md`), add a section on usage, including an example of configuring it as a git merge driver.
