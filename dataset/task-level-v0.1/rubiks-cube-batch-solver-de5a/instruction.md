# Batch Rubik's Cube Solver CLI

A small "cube service" needs a batch tool. Given a folder of cube-state records, produce, for
each record, a legal move sequence that restores the cube to the solved state, and write a
single consolidated JSON report.

The tool must run using the Python standard library only. Do not use any third-party solver
package and do not access the network.

## Entry point

Your program lives at `/app/solution.py` and is invoked as:

```bash
python solution.py --input /app/inputs/data --output /app/result.json
```

It reads every `*.json` file inside the `--input` directory and writes the report to the
`--output` path.

## Input package

Each file under `/app/inputs/data/` is a JSON array of records. A record has:

- `id` — a string key (unique after duplicates are resolved, see below).
- `type` — either `"scramble"` or `"facelet"`.
- `state`:
  - for `"scramble"`: a space-separated move string that, when applied to a solved cube,
    reaches the target state.
  - for `"facelet"`: a 54-character string over the letters `U R F D L B`, read face by face
    in the fixed order `U`, `R`, `F`, `D`, `L`, `B`, with 9 stickers per face listed in
    row-major order (left to right, top to bottom).
- `priority` — optional integer; higher wins when the same `id` appears more than once.
    When absent, treat the priority as `0`.

Scramble depth stays at 8 moves or fewer. This bound applies only to `"scramble"`
records. A `"facelet"` record describes an arbitrary cube state and may lie deeper than
8 moves from solved — your solver must handle facelet states at least 12 moves away
from the solved state.

## Move notation

Moves use Singmaster notation. A move is a face letter in `U D L R F B` with an optional
suffix:

- no suffix — a single clockwise quarter turn (e.g. `R`),
- `'` — a single counter-clockwise quarter turn (e.g. `R'`),
- `2` — a half turn (e.g. `R2`).

Any token outside this scheme is illegal.

## Solved state

The cube is solved when every face shows a single color, i.e. the facelet string equals:

```
UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB
```

## Required behavior

For each unique `id` (after resolving duplicates), emit one object:

- Solvable state:
  `{"id": <id>, "moves": [m1, m2, ...], "solved": true}`, where applying `moves` in order to
  the record's target state yields the solved cube. An already-solved input yields
  `"moves": []`. You do not need the shortest sequence — any legal restoring sequence is
  accepted.
- Invalid state:
  `{"id": <id>, "error": <reason>}`. A `facelet` is invalid if it is not exactly 54 characters,
  contains a character outside `U R F D L B`, or does not contain each of the six letters
  exactly 9 times. A `scramble` is invalid if any of its tokens is not a legal move. The
  process must still finish with exit code 0.

## Duplicate `id` resolution

When the same `id` appears more than once (in one file or across files), keep the record with
the highest `priority`. If `priority` ties, keep the one from the file whose name sorts last;
if still tied, keep the one at the later array index. Only the surviving record is solved and
reported.

## Output

`/app/result.json` is a single JSON array containing one object per unique `id`, sorted by
`id` in ascending lexicographic order.

The tool must be deterministic: running it multiple times on the same input must produce
a byte-identical `result.json`. Make every ordering in your program explicit — the order
in which you search moves, traverse states, and serialize output must be fixed (e.g.
sorted keys, fixed move lists). Do not rely on hash/`set` iteration order or on random
numbers.
