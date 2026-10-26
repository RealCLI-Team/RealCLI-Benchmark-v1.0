# Cutscene asset reconciliation & cleanup tool

A game build step reconciles a directory of **generated** cutscene/timeline asset files against an
upstream content config. For every config entry the tool decides which managed asset files should
exist, deletes the ones that no longer should, records the ones that are missing, and flags entries
that are internally inconsistent. It writes a structured JSON report and leaves the asset directory
in the reconciled state.

## Inputs

The input package lives under `/app/inputs`:

- `/app/inputs/config.json` — an object `{ "entries": [ <entry>, ... ] }`. Each entry has:
  - `id` (string)
  - `key` (string; a valid key must start with `cutscene_`)
  - `enabled` (bool)
  - `use_timeline` (bool)
  - `use_cutscene` (bool)
  - `required` (list of asset paths, relative to the assets root, that the entry expects when enabled)
- `/app/inputs/assets/` — the managed asset root. Managed files are exactly those with extension
  `.playable` or `.prefab`, at any depth under the root. The root also contains:
  - `/app/inputs/assets/.handauthored` — a text file with one relative path per line. Any file listed
    here must never be deleted, even if it looks like an orphan.
  - files with other extensions (for example `.meta`) which are never managed and never deleted.

All paths in the report are relative to the assets root and use `/` as the separator.

## Behavior

Run:

```
python /app/solution.py --config /app/inputs/config.json --assets /app/inputs/assets --out /app/result.json
```

1. **Validation.** An entry is *invalid* if any of the following hold. An invalid entry owns no
   assets and does not itself keep any file alive.
   - `use_timeline` and `use_cutscene` are both `true` → reason `mutex_flags`
   - `key` does not start with `cutscene_` → reason `bad_key`
   - its `id` is shared by more than one entry → reason `duplicate_id` (every entry carrying that id
     is invalid)

   If an entry hits several conditions, emit each reason as its own record. Records are unique per
   `(id, reason)` pair.

2. **Desired set.** For each *valid* entry with `enabled = true`, the managed files it owns are exactly
   its `required` paths. Valid entries with `enabled = false`, and all invalid entries, own nothing.

3. **kept.** Managed files that exist on disk and belong to the desired set.

4. **deleted.** Every managed file (`.playable` / `.prefab`) under the assets root that is not in the
   desired set of any valid enabled entry is deleted, unless its relative path is listed in
   `.handauthored`. Files with non-managed extensions and hand-authored files are left on disk
   untouched. Deleted files are removed from disk.

5. **missing.** For each valid enabled entry, every `required` path that does not exist on disk.

## Output: `/app/result.json`

```json
{
  "kept":    ["<rel path>", ...],
  "deleted": ["<rel path>", ...],
  "missing": [{"id": "<id>", "path": "<rel path>"}, ...],
  "invalid": [{"id": "<id>", "reason": "<reason>"}, ...]
}
```

- `kept` and `deleted` are sorted ascending by path.
- `missing` is sorted ascending by `(id, path)`.
- `invalid` is sorted ascending by `(id, reason)`; `reason` is one of `mutex_flags`, `bad_key`,
  `duplicate_id`.

Running the tool again on the already-reconciled directory must produce a stable report and delete
nothing further (idempotent). `/app/result.json` must keep the report from the first run — do not
overwrite it. To verify idempotency, write the second and third runs to separate paths (e.g.
`/app/_r2.json` and `/app/_r3.json`): those two outputs must be byte-identical, with an empty
`deleted` list, and no files removed.
