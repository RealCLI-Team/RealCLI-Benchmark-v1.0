# Deterministic Dependency Scheduler with Live-Set Peak

A build/dependency planner ingests several graph fragments produced by different upstream
tools and must emit **one deterministic execution plan** together with a reproducible
memory-pressure metric.

Your job is to implement a program at `/app/solution.py` that reads a directory of graph
fragments, merges them into a single directed graph, and writes a JSON result.

## Command-line interface

```
python3 /app/solution.py --input /app/inputs/<case> --output /app/result.json
```

The program reads **every** `*.json` file in the `--input` directory (non-recursively) and
writes the result JSON to `--output`.

## Input fragments

Each `*.json` file in the input directory is one fragment:

```json
{
  "source_id": "alpha",
  "priority": 5,
  "nodes": [{"id": "A", "weight": 3}, {"id": "B", "weight": null}],
  "edges": [{"from": "A", "to": "B"}]
}
```

- The overall graph is the **union** of all fragments.
- The same node `id` may appear in multiple fragments. Merge its `weight` by choosing the
  fragment with the highest `priority`; if priorities tie, choose the fragment whose
  `source_id` is lexicographically smallest.
- A node referenced only inside an `edge` (never declared in any `nodes` list) is created
  with `weight` equal to `null`.
- Edges are unioned and de-duplicated by the `(from, to)` pair. A duplicated edge does not
  change the edge count.
- `weight` may be a non-negative integer or `null` (unknown). `null` is a valid value.
- A node with an empty or missing `id`, or an edge missing `from` or `to`, is **invalid
  input**.

## Output

Write the `--output` JSON object with exactly these keys:

- `status`: one of `"ok"`, `"cycle"`, or `"error"`.
- `order`: the topological order as a list of node ids when `status == "ok"`, otherwise
  `null`. Ordering uses **Kahn's algorithm**. Whenever more than one node has in-degree `0`,
  select the next node by: ascending `weight`, where any numeric weight sorts **before**
  `null`; then ascending `id` as a tie-breaker.
- `cycle_nodes`: when the graph contains a cycle (including any self-loop where
  `from == to`), set `status = "cycle"`, `order = null`, and list every node that still has
  a non-zero remaining in-degree after Kahn's algorithm terminates, sorted ascending by
  `id`. It is an empty list in all other cases.
- `peak_live`: an integer. Replay the emission described by `order`. A node becomes "live"
  the step it is emitted and stays live until every one of its direct successors has also
  been emitted. Concretely, after step `i`'s emission, an emitted node counts toward the
  live total of step `i` iff at least one of its direct successors has not been emitted
  yet — which means a node with no successors never counts as live in any step
  (it contributes 0 to every step's live total). `peak_live` is the maximum number of
  simultaneously live nodes across all steps. When `status != "ok"`, set it to `0`.
- `stats`: an object with `node_count` (distinct merged node ids), `edge_count` (distinct
  merged edges), and `source_count` (number of distinct `source_id` values).
- `errors`: a list of `{"reason": ...}` objects, one for each invalid input encountered. Use
  `"invalid_node_id"` for a node with an empty/missing `id`, and `"invalid_edge_endpoint"`
  for an edge missing `from` or `to`. When this list is non-empty, set `status = "error"`,
  `order = null`, `cycle_nodes = []`, and `peak_live = 0`. Invalid input is checked before
  cycle detection.

## Example

For a fragment with nodes `A(3)`, `B(1)`, `C(2)`, `D(5)` and edges `A->C`, `B->C`, `C->D`:
the ready set starts as `{A, B}`; `B` (weight 1) is emitted before `A` (weight 3), giving
`order = ["B", "A", "C", "D"]`, `peak_live = 2` (after the final step, `D` has no
successors, so it never counts as live; the maximum is reached at step 2, where `A` and
`B` both still have `C` unemitted), and
`stats = {"node_count": 4, "edge_count": 3, "source_count": 1}`.

Sample fragments are available under `/app/inputs/`.
