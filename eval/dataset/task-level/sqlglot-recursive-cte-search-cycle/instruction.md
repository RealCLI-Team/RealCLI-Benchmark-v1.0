# sqlglot swallows SEARCH / CYCLE clauses when handling recursive CTEs

`/app` runs our internal fork of sqlglot, which our SQL governance platform uses for parsing, formatting, and cross-dialect transpilation. We recently onboarded a batch of PostgreSQL / Oracle hierarchical recursive queries that make heavy use of the standard recursive subquery factoring syntax `WITH RECURSIVE ... SEARCH ... / CYCLE ...`, and we hit a pile of problems — we'd like you to straighten out this whole area from the ground up. Below are the symptoms picked out from Slack, the bug tracker, and the production analytics platform.

## A batch of valid SQL fails to parse outright

Both Postgres and Oracle can run these, but sqlglot throws ParseError on all of them and the platform skips the whole batch:

```sql
WITH RECURSIVE sg(id, link) AS (
  SELECT g.id, g.link FROM graph g
  UNION ALL
  SELECT g.id, g.link FROM graph g JOIN sg ON g.id = sg.link)
SEARCH DEPTH FIRST BY id, link SET ordercol
SELECT * FROM sg;
```

```sql
WITH RECURSIVE sg(id, link) AS (...)
SEARCH BREADTH FIRST BY id DESC, link ASC NULLS FIRST SET ordercol
CYCLE id, link SET is_cycle TO 'Y' DEFAULT 'N' USING path
SELECT * FROM sg;
```

```sql
WITH sg(id, link) AS (...)    -- Oracle 不写 RECURSIVE 关键字
SEARCH DEPTH FIRST BY id SET ordercol
CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path
SELECT * FROM sg;
```

Here's a typical request captured in production (an excerpt):

```sql
WITH RECURSIVE search_graph(id, link, data, depth) AS (
  SELECT g.id, g.link, g.data, 1 FROM graph g
  UNION ALL
  SELECT g.id, g.link, g.data, sg.depth + 1
  FROM graph g, search_graph sg
  WHERE g.id = sg.link)
SEARCH DEPTH FIRST BY id SET ordercol
CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path
SELECT * FROM search_graph ORDER BY ordercol;
```

All of the above are forms that actually run on the database side; sqlglot should be able to parse them, output them as-is, and transpile them across dialects.

## Some forms parse "successfully" but their semantics are silently rewritten

Comparing `parse_one(sql).sql()` before and after, we found two classes of serious problems:

- When the input is the full form `CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path`, sqlglot drops `TO ... DEFAULT ...` entirely and only outputs `CYCLE id SET is_cycle USING path`. The generated SQL then executes with changed semantics on the PG side — the value semantics of the cycle mark column are gone, which is a huge trap. This kind of "parses fine, but semantics get eaten" situation absolutely cannot remain; raising an error is preferable.
- When the input is `CYCLE id SET is_cycle TO 'Y'` (only `TO` without `DEFAULT`) or the reverse, the clause is currently silently dropped and parsing continues. Per the SQL standard this is illegal syntax and must raise an error. Likewise: `CYCLE ... USING` (no path column), `SEARCH DEPTH FIRST BY id SET` (no sequence column), `SEARCH FIRST BY id SET seq` (missing `DEPTH`/`BREADTH`), `SEARCH DIAGONAL FIRST BY id SET seq` (illegal kind) — all must raise parse errors.

Ordering must also be enforced: when both clauses coexist, the SQL standard / PG / Oracle all require `SEARCH` first and `CYCLE` second; writing them in reverse (`CYCLE ... SEARCH ...`) is not legal syntax and must raise an error — unlike now, where CYCLE gets followed by a pile of unrecognized tokens and slips through.

## The qualify optimizer completely breaks

Another, trickier case reported by users: before sending user SQL to the executor, the platform runs `sqlglot.optimizer.qualify.qualify` once. The following snippet runs fine in Postgres, but after qualify it either errors or produces semantically wrong SQL:

```sql
WITH RECURSIVE sg(id, link) AS (...)
SEARCH DEPTH FIRST BY id SET ordercol
CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path
SELECT ordercol, is_cycle, path FROM sg WHERE is_cycle = 'N' ORDER BY ordercol;
```

In PostgreSQL semantics, `SEARCH ... SET <序列列>` adds an extra output column to the CTE (here `ordercol`), and `CYCLE ... SET <标记列> ... USING <路径列>` likewise adds two extra output columns (`is_cycle`, `path`); the outer query referencing these columns directly is legal. qualify currently has no idea these synthesized columns exist, so subsequent steps — `SELECT *` expansion, `resolver` column attribution, `pushdown_projections`, etc. — either error or attribute wrong column names. The full `sqlglot.optimizer.optimize` must also pass: after it runs, the `SEARCH` and `CYCLE` clauses must still be present in the output.

To give consumers like qualify / resolver / pushdown a unified entry point for reading columns, both `exp.With` and `exp.CTE` must expose a `synthesized_output_columns()` method: it returns, in a stable order, the list of output column names synthesized by SEARCH/CYCLE — the SEARCH sequence column first, then the CYCLE mark column and the USING path column (with only SEARCH, it contains just the sequence column; if CYCLE has no `USING`, no path column is included; when neither clause exists it returns an empty list, and ordinary recursive CTEs must not be affected). The projection order for `SELECT *` expansion must also be fixed: the CTE's original columns first, synthesized columns appended after in the order above (e.g. the example above expands to `id, link, ordercol, is_cycle, path`).

## Cross-dialect transpilation must preserve and normalize

The platform transpiles PG/Oracle SQL to target dialects such as Snowflake, DuckDB, MySQL, TSQL, BigQuery. Requirements:

- To Oracle: the `RECURSIVE` keyword is preserved exactly as parsed — input that carries `RECURSIVE` (e.g. PG-sourced SQL transpiled to Oracle, and likewise to Snowflake / DuckDB / MySQL / BigQuery) still emits `RECURSIVE` in the output, while Oracle-style input without `RECURSIVE` (`WITH ... (无 RECURSIVE)`) must also be accepted and generated back in the same form (roundtrip keeps it off). The one exception is TSQL: that dialect does not support the `RECURSIVE` keyword, so the output there always omits it.
- To Snowflake / DuckDB / Oracle / TSQL: normalization of `ASC/DESC/NULLS FIRST/NULLS LAST` in SEARCH BY sort keys must be consistent with those dialects' existing `ORDER BY` null-ordering handling — don't redundantly emit the dialect's default NULLS position, but explicitly written non-default NULLS positions must be preserved; when NULLS is not directly supported by the target dialect, simulate it the same way as the repository's existing `ORDER BY` simulation strategy (e.g. MySQL/TSQL use `CASE WHEN col IS NULL THEN 1 ELSE 0 END`).
- To BigQuery: CTE column alias lists are not supported in BigQuery, and the existing code already pushes column aliases down to the inner SELECT in the `WITH x(a, b) AS ...` case; this behavior must not break when SEARCH / CYCLE are present.
- Across dialects, the SEARCH/CYCLE clauses themselves must be preserved as-is and not dropped.

## Generation must be stable

For valid input, feeding the result of `parse_one(sql).sql()` back into `parse_one(...).sql()` should be stable (idempotent). Keyword case and redundant whitespace in input must be normalized; narrow syntax (a single sort key without direction, `CYCLE 单列 SET 标记列 [USING 路径列]`) must behave consistently with the repository's current behavior, and existing regression cases in `/app` such as `TestPostgres.test_recursive_cte` must not regress because of your changes.

## What you should deliver

- Fix every relevant module in sqlglot you can think of. The existing key paths (parsing, generation, optimizer qualify / subquery elimination / pushdown, etc.) must all work together, not just cramming the new syntax into the parser.
- Following the style of the repository's existing dialect test cases, add corresponding automated test cases in the PostgreSQL and Oracle dialect test modules (covering normal parse/generate, cross-dialect transpilation, illegal syntax raising errors, `qualify` preserving the clauses and handling synthesized columns), matching the style of the existing `TestPostgres` / `TestOracle` classes.
- At the AST level, downstream code must be able to explicitly construct nodes with `exp.RecursiveWithSearch` / `exp.RecursiveWithCycle` and generate SQL from them, rather than only obtaining them through string parsing. The two nodes are attached to `exp.With` at `args["search"]` / `args["cycle"]` respectively. The arg layout of `RecursiveWithSearch`: `kind` is `"DEPTH"` / `"BREADTH"` normalized to uppercase; sort keys go in the `expressions` list, each item being an `exp.Ordered` (whose `desc` / `nulls_first` args give the direction and NULLS order respectively); `expression` is the sequence column produced by `SET`. The arg layout of `RecursiveWithCycle`: `expressions` is the detection column list (all columns included), `expression` is the mark column produced by `SET`, and `to` / `default` / `using` correspond to the `TO` value, `DEFAULT` value, and `USING` path column respectively, each directly readable.

The exact full grammar follows the definitions in the "recursive subquery factoring / recursive query" chapters of the PostgreSQL and Oracle manuals — remember to align with both (post SQL:1999 the two vendors are essentially consistent on this part).
