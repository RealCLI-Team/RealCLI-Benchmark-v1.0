# Cube Level Engine: restore solutions and configuration validation

You are building the engine behind a cube puzzle level pipeline. A level editor stores, for
each level, a snapshot of the cube state (a 54-character facelet string) and — optionally — the
move script that was allegedly used to scramble a solved cube into that snapshot. Because the two
artifacts can drift apart when someone hand-edits a level, your engine must both **produce a
restoring move sequence** and **judge whether the configuration is well-formed and self-consistent**.

Write your program at `/app/solution.py`. It must run fully offline using only the Python
standard library. It reads every level file and writes a single report.

## Command line

```
python /app/solution.py --input /app/inputs/data --output /app/result.json
```

- `--input` is a directory. Every `*.json` file in it is one level (one case).
- `--output` is the path of the JSON report to write.

## Level file schema

Each `*.json` file is a single JSON object:

- `case_id` (string, required): unique identifier of the case.
- `cube` (string, required): the facelet state string (see layout below).
- `scramble` (array of strings, optional): a move sequence claimed to transform the **solved**
  cube into `cube`.

## Facelet layout

The cube has six faces: `U` (up), `R` (right), `F` (front), `D` (down), `L` (left), `B` (back).
Each face has 9 stickers labelled row-major (`index_within_face = 3*row + col`, rows top→bottom,
cols left→right when looking straight at that face). The `cube` string concatenates the six faces
in the order **U, R, F, D, L, B**, so the absolute index of a sticker is:

```
U0..U8 -> 0..8      R0..R8 -> 9..17     F0..F8 -> 18..26
D0..D8 -> 27..35    L0..L8 -> 36..44    B0..B8 -> 45..53
```

Unfolded net (numbers are absolute string indices):

```
              0  1  2
              3  4  5
              6  7  8
  36 37 38   18 19 20    9 10 11   45 46 47
  39 40 41   21 22 23   12 13 14   48 49 50
  42 43 44   24 25 26   15 16 17   51 52 53
             27 28 29
             30 31 32
             33 34 35
```

Every sticker character is one of `U R F D L B` (the color that belongs to the face of the same
letter). The **solved** state is:

```
UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB
```

## Move notation

A move is a face letter in `{U, D, L, R, F, B}` with an optional modifier:

- no modifier: a 90° clockwise quarter turn of that face (clockwise as seen looking at the face
  from outside the cube);
- `'` (apostrophe): a 90° counter-clockwise turn (three clockwise quarter turns);
- `2`: a 180° turn (two clockwise quarter turns).

Any other token is illegal (e.g. `Z`, `R3`, `u`, `RR`, empty string).

A single **clockwise quarter turn** permutes stickers along the cycles listed below. A cycle
`(a b c d)` means: the sticker content at absolute index `a` moves to index `b`, `b` moves to `c`,
`c` moves to `d`, and `d` moves to `a` (all cycles of a move are applied simultaneously). Indices
not listed for a move are unchanged.

```
U: (0 6 8 2)(1 3 7 5)(9 45 36 18)(10 46 37 19)(11 47 38 20)
R: (2 20 29 51)(5 23 32 48)(8 26 35 45)(9 15 17 11)(10 12 16 14)
F: (6 44 29 9)(7 41 28 12)(8 38 27 15)(18 24 26 20)(19 21 25 23)
D: (15 51 42 24)(16 52 43 25)(17 53 44 26)(27 29 35 33)(28 32 34 30)
L: (0 18 27 53)(3 21 30 50)(6 24 33 47)(36 38 44 42)(37 41 43 39)
B: (0 42 35 11)(1 39 34 14)(2 36 33 17)(45 47 53 51)(46 50 52 48)
```

A `'` move applies the same cycles three times; a `2` move applies them twice.

## Required behavior per case

Evaluate each case with the following priority (earlier rules win):

1. **Malformed `cube`** → `status = "invalid"`. `cube` is malformed if its length is not 54, or it
   contains any character outside `U R F D L B`, or any of the six colors does not appear exactly
   9 times.
2. **Bad `scramble`** (only when `scramble` is present):
   - if any token in `scramble` is not a legal move → `status = "invalid"`;
   - otherwise apply `scramble` to the solved cube to obtain `expected`; if `expected != cube`
     the configuration is inconsistent → `status = "invalid"`.
3. **Already solved**: if `cube` equals the solved state → `status = "already_solved"`,
   `solution = []`, `move_count = 0`.
4. **Otherwise** → `status = "solved"`. `solution` is a move sequence (array of strings) that, when
   applied in order to `cube`, restores it to the solved state. `move_count = len(solution)`.

The restoring solution does not need to be the shortest, but it must be **canonical**: no two
adjacent moves in `solution` may act on the same face (so sequences such as `["U", "U"]`,
`["U", "U'"]`, `["U2", "U"]` are not allowed as consecutive pairs).

## Output report

Write `--output` as a JSON object whose keys are the `case_id` values.

- For `status = "solved"`: `{"status": "solved", "solution": [...], "move_count": <int>}`.
- For `status = "already_solved"`: `{"status": "already_solved", "solution": [], "move_count": 0}`.
- For `status = "invalid"`: `{"status": "invalid"}` (no `solution` / `move_count` fields).

The top-level object must list its `case_id` keys in ascending (lexicographic) order. Running the
program twice on the same input must produce a byte-for-byte identical report.
