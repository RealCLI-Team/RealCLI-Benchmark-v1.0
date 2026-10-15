# Audit-Log Command Gate: normalize bash commands into a security report

A code-agent platform records every bash command its assistants tried to run into
per-agent audit logs. The security team runs a static gate: each command is matched
against an explicit allow-list policy and classified as an allowed prefix, a
command-injection attempt, or an unrecognized command that needs manual approval.

Your job is to build the offline analyzer that turns the raw audit logs into a
normalized security report. The analyzer must not execute any command; it is a pure
static string analysis driven entirely by the policy file below.

## Input package (`/app/inputs`)

- `/app/inputs/policy.json`:
  ```json
  {
    "allowed_prefixes": ["cat","cd","ls","mkdir","tail","grep","docker kill",
      "git commit","git diff","git log","git status","npm run lint","pnpm add","python3","gh pr create"],
    "injection_markers": [";","|","&&","||","&","`","$(","<(",">(","#"]
  }
  ```
- `/app/inputs/logs/agent-a.jsonl`, `/app/inputs/logs/agent-b.jsonl`: one JSON object per
  line with fields `record_id` (string), `source` (string), and `command` (string).
  Some lines are intentionally malformed JSON; some objects miss `command`; some
  `command` values are the empty string `""`. A record may also carry an extra
  `claimed_prefix` field — it is **not** authoritative and must be ignored.

## Program

Implement `solution/solve.py`, runnable as:

```
python3 solution/solve.py --policy /app/inputs/policy.json --logs /app/inputs/logs --out /app/result.json
```

`--logs` points to a directory; process every `*.jsonl` file inside it in ascending
filename order. For each file the `source` used for reporting is that record's `source`
field for valid records, and the log filename **without** its `.jsonl` extension (its
stem, e.g. `agent-a`) for invalid lines.

## Verdict policy

For every valid record compute a `verdict` from `command` using these steps in order:

1. **Scan for injection first.** Tokenize the command with shell quoting rules: text
   inside single quotes `'...'`, inside double quotes `"..."`, or inside a heredoc body
   introduced by `<<TAG` is treated as literal. If any `injection_markers` entry appears
   **outside** quoted / heredoc regions, the verdict is `command_injection_detected`.
   Plain file redirections written with `>`, `>>`, `2>`, or `<` to a filename are **not**
   injection and never appear in the marker list.
2. **Otherwise match the longest allowed prefix.** A prefix `P` matches when the command
   equals `P` or starts with `P` followed by a space, compared on whole tokens (so
   `catalog` does not match `cat`, and `github` does not match `git`). Leading
   `NAME=value` environment assignments are part of the command and must be kept when
   comparing: e.g. `PYTHONPATH=/tmp python3 x.py` matches `python3` only after skipping
   the assignment, and the reported prefix keeps the assignment, i.e.
   `PYTHONPATH=/tmp python3`. Among all matching prefixes choose the longest one; the
   verdict is that prefix string.
3. **Otherwise** the verdict is `none`.

## Output (`/app/result.json`)

```json
{
  "records": [{"record_id": "...", "source": "...", "verdict": "...", "prefix": "cat"}],
  "summary": {"total": 0, "by_verdict": {"<verdict>": 0},
              "injections": 0, "unresolved": 0, "invalid_records": 0},
  "invalid": [{"source": "...", "line": 0, "reason": "malformed_json"}]
}
```

Rules:

- `prefix` is the matched prefix string when the verdict is a prefix; it is `null` when
  the verdict is `command_injection_detected` or `none`.
- `records` is sorted by `(source, record_id)` ascending.
- An empty-string command is a **valid** record and resolves to `none`.
- A line that is malformed JSON goes to `invalid` with `reason` `"malformed_json"`.
  A JSON object that has no `command` field goes to `invalid` with `reason`
  `"missing_command"`. Invalid entries never appear in `records`. Each invalid entry
  records its 1-based `line` number within its own file. Blank lines are ignored (they
  are neither valid records nor invalid entries).
- `invalid` is sorted by `(source, line)` ascending.
- Records are keyed by `(source, record_id)`. The same `record_id` appearing in two
  different files (different `source`) yields two distinct rows. Within a single source a
  duplicate `record_id` keeps the **last** occurrence and drops the earlier ones.
- `summary.total` equals the number of rows in `records`.
- `summary.by_verdict` maps each verdict string to its count in `records`.
- `summary.injections` counts `command_injection_detected` verdicts.
- `summary.unresolved` counts `none` verdicts.
- `summary.invalid_records` equals the number of entries in `invalid`.
