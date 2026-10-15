# Batch Rubik's Cube Solving Service

You are building the offline batch module for a Rubik's Cube app backend. Given a batch of
3×3 cube state configurations uploaded by users, the module must produce, for each state, a
sequence of moves that restores it to the solved state, then write a structured result file
that the frontend can render per record.

Everything runs offline with the Python standard library only. Do not rely on any external
solving library or network access.

## Facelet and move conventions

A cube state is a **54-character facelet string**. The six faces appear in the fixed order
`U R F D L B`, and each face contributes 9 characters in row-major order (indices 0..8),
giving these global index ranges:

- `U` = indices 0–8
- `R` = indices 9–17
- `F` = indices 18–26
- `D` = indices 27–35
- `L` = indices 36–44
- `B` = indices 45–53

Colors are written using the letter of the face they belong to on a solved cube. The solved
state is exactly:

```
UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB
```

The six basic moves are `U D L R F B`, each meaning a 90° clockwise turn of that face when
viewed from outside the cube. Modifiers:

- no modifier → 90° clockwise
- `'` → 90° counter-clockwise
- `2` → 180°

This yields exactly 18 legal move tokens:
`U U' U2 R R' R2 F F' F2 D D' D2 L L' L2 B B'` and `B2`.

The full set of conventions above is also provided as data in `/app/inputs/data/notation.json`
(face order, index ranges, solved state, legal move tokens).

## Input package

`/app/inputs/data/cubes.jsonl` — one JSON object per line:

```
{"id": "<string>", "state": "<54-char facelet string>"}
```

`/app/inputs/data/notation.json` — the facelet/move conventions described above.

## What to build

Create `/app/solution.py`. It must accept:

```
python solution.py --input /app/inputs/data --output /app/result.json
```

It reads `cubes.jsonl` from the `--input` directory and writes the result JSON to the
`--output` path.

## Output format

`result.json` has a single top-level key `results` whose value is a list of records:

```json
{
  "results": [
    {"id": "...", "status": "...", "moves": ..., "move_count": ...}
  ]
}
```

Per-record fields:

- `id` — the record id (string).
- `status` — one of `"solved"`, `"already_solved"`, `"invalid"`.
- `moves`:
  - when `status` is `"solved"`: a **non-empty** list of legal move tokens that, applied in
    order to that record's input `state`, produces the solved state.
  - when `status` is `"already_solved"`: an empty list `[]`.
  - when `status` is `"invalid"`: `null`.
- `move_count`:
  - equal to `len(moves)` when `moves` is a list.
  - `0` when `moves` is `null`.

## Status rules

- `already_solved`: the input `state` is exactly the solved string.
- `invalid`: the input `state` cannot form a reachable, well-formed cube — i.e. its length is
  not 54, it contains any character outside `U R F D L B`, or any of the six colors does not
  appear exactly 9 times. Such records get `status="invalid"`, `moves=null`, `move_count=0`,
  and must never be reported as solved.
- `solved`: a well-formed, non-solved state; `moves` is a valid restoring sequence. Any
  correct restoring sequence is acceptable — it need not be the shortest.

## Deterministic ordering and duplicate ids

- The `results` list must be sorted by `id` in ascending (lexicographic) order, and each `id`
  must appear **exactly once**.
- A duplicated `id` (the same `id` appearing on more than one line, regardless of state) is
  treated as an input conflict: it collapses into a single record with `status="invalid"`,
  `moves=null`, `move_count=0`.

Every emitted move token must be one of the 18 legal tokens declared above.
