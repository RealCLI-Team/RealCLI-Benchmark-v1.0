Our team uses Hatch across the board to manage Python projects. The single most annoying daily task for everyone who uses it is adding or removing dependencies on an existing project: either you open `pyproject.toml` and edit by hand — one slip and the TOML syntax breaks — or you forget where exactly it should go: `project.dependencies`, some optional feature, some dependency group, some Hatch environment's dependencies, or that environment's `extra-dependencies`? After adding to the wrong place, running it produces another pile of environment-inconsistency errors. Manual editing also easily messes up the original hand-written comments, indentation, and inline-array formatting, and then code review turns into another round of arguing over irrelevant formatting diffs.

The Hatch source is at `/app`, already installed in editable mode, and the `hatch` command works directly. Please extend the `hatch dep` command group so that "add/remove a dependency on the project / on a feature / on a group / on an environment" can be done right from the command line, without opening `pyproject.toml` anymore. After it's done, the usage we expect looks roughly like this:

```
hatch dep add "requests>=2"
hatch dep add pytest -o test
hatch dep add ruff -g lint
hatch dep add ipython --env default
hatch dep add debugpy --env lint --extra
hatch dep add "requests>=2" --dry-run
hatch dep add "click>=9" --force

hatch dep remove requests
hatch dep remove pytest -o test
hatch dep remove coverage -g dev
hatch dep remove ipython --env default
```

For the expected behavior of these two commands, just follow the repo's existing conventions, including but not limited to:

- When editing `pyproject.toml`, **preserve the original formatting and comments** (inline arrays stay inline, multi-line arrays stay multi-line; comments, blank lines, and indentation untouched). If any validation step fails, it must exit non-zero while leaving no trace of writing — `pyproject.toml` must not change by a single byte, and no temp files may be left behind. When a field or table doesn't exist yet and has to be created, follow these layout rules: a newly created field is written as an inline array and inserted at the end of the table it belongs to; a newly created table is appended to the end of the file, preceded by one blank line. In newly written requirement entries, marker quoting is normalized to single quotes (e.g. `requests>=2; python_version >= '3.9'`). Note that a dependency-group array may also contain PEP 735 table entries such as `{include-group = "lint"}` alongside plain string requirements — those entries are not dependencies, so add/remove must skip over them (never treat them as invalid) and leave them exactly as they were, operating only on the string entries.
- When judging "is it the same dependency" or "should it count as a duplicate name", use the normalization rules of the distribution name itself, independent of extras, version constraints, and markers.
- Command registration, error-message wording, success-output channel, and the calling style of things like `app.abort` / `app.display_success` must align with how the repo's existing commands `hatch dep show` / `hatch dep hash` / `hatch config set` / `hatch version` are written — don't invent a new style of your own.
- Don't break the repo's own test suite; and don't bypass the ready-made name-normalization utilities in `hatchling` in your implementation.
- `--dry-run` should show in a human-readable way what changes this call **would bring if written to disk**, rather than actually writing. The display format is fixed as a unified diff of `pyproject.toml` before and after modification: it must have the two file headers `--- a/pyproject.toml` and `+++ b/pyproject.toml`, with changed lines prefixed by `-`/`+` marking old lines / new lines.
- The output wording on successful add/remove must make it obvious at a glance which field was touched; the wording is fixed as ``Added `<依赖原文>` to field `<字段路径>` `` and ``Removed `<依赖名>` from field `<字段路径>` ``; where `<字段路径>` is the full path of the TOML field that was modified — the forms `project.dependencies`, `project.optional-dependencies.<feature>`, `dependency-groups.<group>`, `tool.hatch.envs.<env>.dependencies`, `tool.hatch.envs.<env>.extra-dependencies` (e.g. ``Added `requests>=2` to field `project.dependencies` `` and ``Removed `requests` from field `project.dependencies` ``).
- When `hatch dep remove` is run without any of the scoping flags `-o/--optional`, `-g/--group`, `--env`, the default scope is **all project-level metadata fields**: `project.dependencies`, every `project.optional-dependencies.<feature>`, and every `dependency-groups.<group>` — dependencies with the same name must all be removed, printing one ``Removed ... `` line per removal. Environment dependencies under `tool.hatch.envs.*` are **not** in the default scope; to remove a dependency inside an environment you must pass `--env` explicitly. With a scoping flag, removal only happens within the corresponding single field.
- `hatch dep add` accepts **multiple requirements** in a single call (e.g. `hatch dep add "requests>=2" "coverage>=7"`), and the call is **atomic**: if any one requirement is invalid or conflicts, the command exits non-zero and `pyproject.toml` is not written at all — not even for the requirements that would have succeeded on their own.
- When an added requirement's name conflicts with an existing entry in the target field (same normalized distribution name), the command must fail with an error message that contains the literal text `--force`; when `--force` is passed, the old entry is replaced **in place at its original position** by the new requirement — no appending, no reordering of the remaining entries.
- `hatch dep remove` likewise accepts **multiple names** in a single call and is likewise atomic: if any one name is not found in scope, nothing is written. The arguments to `remove` must be bare distribution names; passing a string carrying a version constraint (e.g. `requests>=2`) is an error.
- The key names of newly created features and groups are normalized the same way distribution names are, before being written (e.g. `-o Docs` creates `project.optional-dependencies.docs`, and `--group Test` creates `dependency-groups.test`).
- Only the `default` environment may be auto-created by `hatch dep add --env`; targeting any other environment that isn't already defined must be rejected with an error. `--env` and `-o/--optional` are mutually exclusive, and `--extra` is only valid together with `--env`.
- Fields declared via `project.dynamic` must refuse edits, and the error message must contain the word `dynamic`; when the `[project]` table is missing, writing to a `project.*` field is an error (writing to `dependency-groups` is still allowed).

Finally, add a usage doc page for the new commands under `docs/how-to/environment/`, and hook it into an appropriate spot in the mkdocs navigation.
