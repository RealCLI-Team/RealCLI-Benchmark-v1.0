# Add a "see the spec's problems" lint switch to Altair

`/app` holds the source of the Altair we use internally (a declarative statistical visualization library, the Python frontend for Vega-Lite), currently packaged against Vega-Lite v6. The most troublesome class of problem the team hits in day-to-day charting is **the code doesn't blow up, and the chart silently doesn't come out** — the mistake happens at the Python layer, but it's only seen in the browser or at Vega-Lite compile time. A few pitfalls we've actually stepped on:

- Someone wrote `alt.Chart(df).mark_point().encode(x='a:Q', colour='c:N')` — the channel name slipped into `colour`, Python never said a word, `to_dict()` produced a spec, the frontend silently dropped it, and the chart just wouldn't get colored;
- A misspelled aggregate name in `encode(y=alt.Y('b:Q', aggregate='wibble'))`, or an invented word like `timeUnit='fortnight'` — `to_dict()` can generate the spec; the bad news only explodes at schema validation or Vega-Lite compile time, with a meaningless stack;
- A broken shorthand string like `encode(x='a:X')` — today the `ValueError` only comes at `to_dict()`, and the traceback doesn't tell you which chart or which channel;
- `scale={'type':'log'}` paired with a nominal field, or a `time` scale paired with a quantitative field — the browser goes straight to blank, without a single hint;
- Parameter name collisions between subplots in a composed view, `x2` written without `x`, a misspelled mark, `stack='sideways'`, `sort='sideways'`, `bin='yes'`, `aggregate='sum'` together with `bin=True`... we run into these over and over while editing code.

I want a static checking tool **built into** the library — no rendering, no data access, no dependency on any backend — that walks the chart spec and finds these problems. Implement it in the `/app` source tree, and **also wire it into the existing serialization path**, so that flipping the switch blocks bad charts instead of waiting for downstream to blow up.

## How I expect to use it

```python
import altair as alt

chart = alt.Chart(...).mark_point().encode(colour='c:N', y=alt.Y('b:Q', aggregate='wibble'))

# 单独查体
issues = alt.lint(chart)          # 顶层函数
issues = chart.lint()             # 每种图表对象上的方法,与顶层函数等价

# 一开挡回来
chart.to_dict(strict_lint=True)   # 有 E 级问题 → alt.LintError
chart.to_json(strict_lint=True)   # 同上

# 出问题时明确知道是啥类型
try:
    chart.to_dict(strict_lint=True)
except alt.LintError as err:
    for issue in err.issues:
        print(issue)              # E003 [error] encoding.y: 'wibble' is not ...
```

`altair.lint` / `altair.LintIssue` / `altair.LintError` must be accessible directly from the top-level namespace and appear in `altair.__all__`. The `.lint()` method must be callable on all top-level chart classes (`Chart`, `LayerChart`, `ConcatChart`, `HConcatChart`, `VConcatChart`, `FacetChart`, `RepeatChart`), equivalent to the top-level function.

## Input & output

- The input may be any of the chart objects above, or a plain `dict` (an Altair/Vega-Lite spec dictionary). **Any other type** (string, number, `None`, list, etc.) gets `TypeError`.
- The check **never modifies** its input; repeated calls on the same input must produce identical results.
- The return value is a list of result objects, sorted in lexicographic order by `(path, code)`; an empty list when there are no problems.
- Each result object exposes at least four attributes:
  - `code`: string, the rule number. `E` prefix means error (will break the chart), `W` prefix means warning (the chart renders but the semantics are questionable);
  - `severity`: `"error"` / `"warning"`, consistent with the `code` prefix;
  - `path`: string locating the problematic node; the root chart uses the empty string `""`;
  - `message`: a human-readable description; for any rule triggered by a specific value (unknown mark, aggregate, scale type, etc.), the value must be visible in the `message`, so it's easy to trace back which pit it was.
- `str(issue)` must show the code, severity, and location at a glance.

## What shape `path` takes

A few examples — infer the pattern yourself:

- Encoding problems in a unit chart: `encoding.x`, `encoding.colour`;
- List-typed channels (e.g. `tooltip=['a:Q','b:Q']`) are checked item by item, with an index in the path: `encoding.tooltip[1]`;
- Mark problems: `mark`;
- Parameter problems: `params[0]`, `params[1]`;
- Subplots: `layer[0].encoding.y`, `concat[1].layer[0].encoding.y`, `hconcat[0].mark`;
- Facet views: the inner view path segment is `spec`, e.g. `spec.encoding.x`; facet channel mappings go by `facet.row` / `facet.column` / `facet.facet`; when `facet` is directly a field shorthand (e.g. `.facet('c:N')`), the path is just `facet`.
- Repeat views likewise go through `spec`.

## How shorthand is parsed

Altair channels can be described both with shorthand (e.g. `'sum(b):Q'`) and with explicit properties (`aggregate=...`):

- Shorthand parsing must stay consistent with the library's existing `altair.utils.core.parse_shorthand` (same grammar, same typecode mapping);
- Explicit properties override same-named values parsed out of the shorthand;
- Giving both `shorthand` and `field` on the same channel is **illegal** (the existing serialization path already blows up on this — the linter must surface this error too);
- A channel given `value` or `datum` and no `field` is a constant channel; skip all field-related checks;
- A `condition`-only branch definition (no field/value/datum of its own) is not checked in depth;
- The check as a whole must not actually call `to_dict()` to catch schema errors; nor may it discover problems by relying on exceptions raised during serialization.

`parse_shorthand` currently only raises `ValueError` in a few situations, but in the linter we want diagnostics rather than `try/except`. Add an optional parameter to `parse_shorthand` so that in "collect mode" it returns error information as structured data instead of raising; the default behavior stays completely unchanged (all existing call sites and doctests must run through untouched, byte for byte). The linter internally uses "collect mode" to avoid exception-based control flow.

## Rule list (the numbering is a stable contract — don't reshuffle it)

**Errors (will break the chart or get the spec rejected)**

- `E001` — a key that is not a Vega-Lite channel appears in the encoding (the legal channel set is per the schema shipped with this repo: the union of the properties of `Encoding` and `FacetedEncoding`);
- `E002` — a unit chart (non-composed node) has no mark, or the mark type is not in the legal mark set (when the mark is a `{'type': ...}` dict, look at `type`; composed marks `boxplot`/`errorbar`/`errorband` also count as legal);
- `E003` — the parsed `aggregate` is not among the legal `AggregateOp`s;
- `E004` — the parsed `timeUnit` is not among the legal `TimeUnit`s (including utc-prefixed forms and binned forms);
- `E005` — the parsed `type` is not one of `quantitative`/`ordinal`/`temporal`/`nominal`/`geojson`;
- `E006` — `scale.type` is not a legal `ScaleType`;
- `E007` — scale type mismatches field type: `log`/`pow`/`sqrt`/`symlog` paired with `nominal`/`ordinal`/`geojson`; `time`/`utc` paired with non-`temporal`; `sequential` paired with `nominal`/`geojson`. Don't report when the field type is unknown;
- `E008` — the `stack` value is not one of `True`/`False`/`None`/`"zero"`/`"center"`/`"normalize"`;
- `E009` — the shorthand cannot be parsed by the library's grammar, or the same channel was given both `shorthand` and `field`;
- `E010` — the same channel has both a non-empty `aggregate` and a truthy `bin` (`True` or a bin parameter dict; the string `"binned"` doesn't count);
- `E011` — `sort` is a string that is neither `"ascending"`/`"descending"` nor a legal sort-by-channel (channel names may carry a leading `-`; the channel set matches the schema's `SortByChannel`/`SortByChannelDesc`); don't report when `sort` is a list or dict;
- `E012` — the same parameter name is defined multiple times across the whole chart tree (including subplots at all levels); the first occurrence is not reported, each subsequent duplicate is reported once;
- `E013` — `bin` is a string but not equal to `"binned"`;
- `E014` — after parsing, the channel definition has neither `field`, nor `value`, nor `datum`, and `aggregate` is not `"count"` (count is allowed to have no field).

**Warnings (the chart renders but the semantics are most likely wrong)**

- `W001` — `x2`/`y2`/`longitude2`/`latitude2` is present but the corresponding `x`/`y`/`longitude`/`latitude` is missing; reported on the secondary channel's own path;
- `W002` — a unit chart has no encoding channels at all; reported on that unit chart's own path; `geoshape` is exempt (it draws geometry directly); inheriting a shared encoding from a parent layer etc. also doesn't count as empty;
- `W003` — a channel has `field` or `aggregate` but no explicit `type` (the type can only be inferred from data).

## Wiring into the existing serialization path

- All top-level chart classes' `to_dict()` (and `to_json()` — it delegates to `to_dict()`) gain a new keyword-only parameter `strict_lint: bool = False`:
  - Default `False`, behavior **exactly the same as today** — all existing calls, existing tests, and existing doctests are unaffected.
  - With `strict_lint=True`, run lint before doing the real schema validation; as long as there is any `E`-level issue, raise `altair.LintError` right away (it inherits from `ValueError`, carries an `issues` attribute holding all E-level `LintIssue`s, and `args[0]` is a readable multi-line text printing each one); with no E-level issues (empty, or only W's), serialization continues normally.
  - `W`-level issues never cause an exception because of `strict_lint=True`.
- `strict_lint=True` must propagate into subplots' `to_dict`: charts nested inside layer/concat/facet/repeat must each catch the problems within their own scope, rather than only looking at the root node.
- `LintError` is a subclass of `ValueError` (users can keep catching it with `except ValueError` as before); it must be accessible from the `altair` top level.

## Other requirements

- A chart object may be in an "invalid but not yet serialized" state (e.g. `chart.mark` was directly assigned some garbage string); the checker must handle this, not blow up first itself.
- Channels in facet mappings (`row`/`column`/`facet`) go through channel checks too; shared encodings on composed nodes like layer must also be checked.
- Repeated calls, and normal serialization of a chart after checking it, must not be affected.
- The library's existing basic usage (building charts, `to_dict()`, `to_json()`, `save`, common shorthands) must not be affected, and all existing `parse_shorthand` doctests keep passing.
- It's a good idea to write your own positive and negative examples to exercise every rule, covering both "this one must be reported" and "this one must not be a false positive".
