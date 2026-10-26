# Value-Domain Closure Analyzer for Game Config Parameters

A game-config toolchain keeps a list of parameter **value-domain gaps** — configuration
fields that still need a legality rule. For each gap you must decide, **from the actual
source code**, whether its set of legal values can be closed (a finite legal set can be
enumerated), can only be validated by reference-table existence, is genuinely open, or
whether the gap entry is stale because the field does not exist at all.

You will build a static analyzer that reads the gap list plus a small C#-style codebase and
emits a deterministic structured report.

## Inputs (`/app/inputs/data/`)

- `gaps.json` — a JSON object:
  ```json
  { "gaps": [
      { "domain": "buff", "category": "enum", "key": "SquadType", "runtimeClass": "AddBuffWithSoldierTypeBuff" }
  ] }
  ```
  Each gap has `domain`, `category`, `key`, and `runtimeClass`. The `category` field is
  informational only — **you must not trust it**; decide the status purely from the code.
- `code/` — one or more `*.cs` text files. They contain:
  - enum definitions, e.g. `public enum SquadType { Melee, Ranged = 3, Cavalry }`
  - classes with single inheritance, e.g. `public class AdjustReceiveHealBuff : AdjustDamageBuff { ... }`
  - field declarations, e.g. `public SquadType SquadType;`, `public int Priority;`,
    `public string DataType;`
  - reference fields marked by a trailing line comment, e.g.
    `public int SoldierType; // ref:SoldierTable`
  - fields declared only on a base class and inherited by a subclass

## Output (`/app/result.json`)

Write a JSON object with this exact shape:

```json
{
  "records": [
    { "domain": "...", "runtimeClass": "...", "key": "...",
      "status": "closed-enum",
      "allowed": ["..."],
      "evidence": "..." }
  ],
  "summary": { "total": 6, "closed-enum": 2, "closed-reference": 1, "open": 2, "stale": 1 },
  "unclosable_value_domains": ["domain/runtimeClass/key"]
}
```

## Classification rules

For every gap, resolve and classify it as follows:

1. **Field resolution.** Look for a field declared as `public <Type> <key>;` on
   `runtimeClass`. If it is not declared there, follow the `: Base` inheritance chain and
   search each base class in turn. If the field is not found on the class or any of its
   ancestors → `status = "stale"`, `allowed = null`.
2. **Enum closure.** If the resolved field's `<Type>` is an enum defined anywhere under
   `code/` → `status = "closed-enum"`, and `allowed` is the list of enum member names sorted
   in ascending (lexicographic) order. Drop any `= value` assignment from a member name.
3. **Reference closure.** Otherwise, if the resolved field's declaration line carries a
   trailing `// ref:<Table>` comment → `status = "closed-reference"`, `allowed = null`, and
   the evidence must name `<Table>`.
4. **Open.** Otherwise (a numeric or plain string field with neither an enum type nor a
   `// ref:` comment) → `status = "open"`, `allowed = null`.
5. **Evidence.** `evidence` is a short human-readable string that includes the name of the
   `.cs` file where the resolving fact was found (the file declaring the field), plus the
   reason. For a `stale` record there is no such file, so `evidence` must state that the
   field was not found on the class or its base classes.
6. **De-duplication.** Collapse gaps that share the same `(domain, runtimeClass, key)`
   triple into a single record.
7. **Ordering and summary.**
   - Sort `records` ascending by the triple `(domain, runtimeClass, key)`.
   - `unclosable_value_domains` lists only the `open` records, each formatted as
     `"domain/runtimeClass/key"`, sorted ascending.
   - `summary.total` is the number of de-duplicated records; the remaining keys
     (`closed-enum`, `closed-reference`, `open`, `stale`) count records by their status.

## How it is run

When run with no arguments, the analyzer must read `/app/inputs/data` and write the report
to `/app/result.json`. The analyzer must also be invocable directly as:

```bash
python /app/solution.py --input /app/inputs/data --output /app/result.json
```

where `--input` points at a directory that contains `gaps.json` and a `code/` subdirectory.
