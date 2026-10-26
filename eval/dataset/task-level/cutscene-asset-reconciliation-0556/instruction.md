# Config-driven asset reconciliation & cleanup tool

A game build step keeps a directory of **generated** cutscene/timeline assets in sync with an upstream
content config. Your job is to write a deterministic Python tool that reconciles the asset directory
against the config: it decides which managed files should exist, deletes the ones that no longer should,
records the ones that are still missing, flags config entries that are internally inconsistent, and writes
a structured JSON report.

## Command

Provide a program at `/app/solution.py` that is invoked exactly like this:

```
python solution.py --config inputs/data/config.json --assets inputs/data/assets --out result.json
```

- `--config` points at the upstream content config (JSON).
- `--assets` points at the managed asset root directory. The tool reconciles this directory **in place**.
- `--out` is the path of the JSON report to write.

All paths in the report are **relative to the assets root** and use `/` as the separator.

## Input package (`/app/inputs/data/`)

### `config.json`

```json
{ "entries": [ { "id": "...", "key": "...", "enabled": true, "use_timeline": false, "use_cutscene": true, "required": ["..."] } ] }
```

Each entry object has:

- `id` (string): entry identifier.
- `key` (string): must start with the prefix `cutscene_`.
- `enabled` (bool): whether the entry is active.
- `use_timeline` (bool) and `use_cutscene` (bool): two mutually exclusive rendering modes.
- `required` (list of strings): relative asset paths this entry expects to exist when it is enabled.

### `assets/`

The managed asset root. **Managed files** are exactly the files whose name ends with `.playable` or
`.prefab`, located anywhere under the assets root (including subdirectories). The directory also contains:

- files that belong to live entries,
- orphan managed files left over from removed or disabled entries,
- files with other extensions (for example `.meta`) — these are **never** managed and must never be deleted,
- a text file `assets/.handauthored` listing one relative path per line (blank lines and lines starting with
  `#` are ignored). Any managed file listed there is **hand-authored** and must never be deleted, even if it
  is an orphan.

## Reconciliation rules

1. **Desired set.** For every entry that is both valid (see rule 2) and `enabled == true`, the managed files
   it owns are exactly the paths in its `required` list. Entries that are disabled or invalid own nothing.

2. **Invalid entries.** An entry is invalid — it owns nothing, triggers no deletion, and produces no
   `missing` records — when any of the following hold. Each triggered condition produces its own reason:
   - `use_timeline` and `use_cutscene` are both `true` → reason `"mutex_flags"`.
   - `key` does not start with `cutscene_` → reason `"bad_key"`.
   - two or more entries share the same `id` → reason `"duplicate_id"` for **every** entry carrying that id.

   If a single entry triggers several conditions, emit one record per triggered reason. The `invalid` list
   contains **unique** `{id, reason}` records (deduplicated).

3. **Missing.** For each valid enabled entry, every path in its `required` list that does not exist on disk
   is reported as a `missing` record `{ "id": <entry id>, "path": <rel path> }`.

4. **Deleted (orphan cleanup).** Every managed file (`.playable` / `.prefab`) under the assets root that is
   **not** in the desired set of any valid enabled entry must be deleted from disk, unless it is listed in
   `assets/.handauthored`. Files with non-managed extensions and hand-authored files are kept untouched.

5. **Kept.** Managed files that exist on disk and are in the desired set.

## Output: `result.json`

Write a JSON object with exactly these four fields. Every list is sorted ascending by the stated key:

```json
{
  "kept":    ["<rel path>", "..."],
  "deleted": ["<rel path>", "..."],
  "missing": [{"id": "<id>", "path": "<rel path>"}, "..."],
  "invalid": [{"id": "<id>", "reason": "<reason>"}, "..."]
}
```

- `kept`: sorted ascending by path.
- `deleted`: sorted ascending by path (these files are removed from disk during the run).
- `missing`: sorted ascending by `(id, path)`.
- `invalid`: sorted ascending by `(id, reason)`; `reason` is one of `"mutex_flags"`, `"bad_key"`,
  `"duplicate_id"`.

All paths in the report must be relative to the assets root; they must not be absolute paths, contain user
names, or contain URLs.

## Idempotency

Running the tool a second time on the already-reconciled directory must delete no further files. In that
second run, `kept`, `missing`, and `invalid` are unchanged from the first run, and `deleted` is empty.
