# Reconciliation report format (/app/result.json)

The report is a single JSON object with exactly these keys:

```json
{
  "kept": ["<rel path>", "..."],
  "deleted": ["<rel path>", "..."],
  "missing": [{"id": "<entry id>", "path": "<rel path>"}, "..."],
  "invalid": [{"id": "<entry id>", "reason": "<reason>"}, "..."]
}
```

- `missing`: for each valid+enabled entry, every required path that does not
  exist on disk — one object `{"id": <entry id>, "path": <rel path>}` per
  missing path (NOT a bare string list).
- `invalid`: one object `{"id": <entry id>, "reason": <reason>}` per violated
  rule; `reason` is one of `mutex_flags | bad_key | duplicate_id`.
- Sort order: `kept` and `deleted` ascending by path; `missing` ascending by
  (id, path); `invalid` ascending by (id, reason).
