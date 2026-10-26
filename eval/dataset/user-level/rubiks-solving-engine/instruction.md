# Rubik's Cube Solving Engine

You are building the solving engine for a small 3x3 Rubik's Cube app. The app stores saved cube
configurations and ships a move-notation config file. For each saved configuration your engine must
produce a sequence of standard face turns that, when applied to that configuration, returns the cube
to the fully solved state (every face a single uniform color).

## Input package (`/app/inputs`)

### `/app/inputs/notation.json`
Defines the legal move alphabet:
- Faces: `U`, `D`, `L`, `R`, `F`, `B`.
- A bare face letter (e.g. `R`) means a 90° clockwise quarter turn of that face.
- A trailing `'` (e.g. `R'`) means a 90° counter-clockwise quarter turn.
- A trailing `2` (e.g. `R2`) means a 180° half turn.
- The `tokens` array lists every legal move token. Any token outside this alphabet is illegal.

### `/app/inputs/puzzles/<id>.json`
Each puzzle file has the shape:
```
{"id": "<string>", "faces": {"U": [[..],[..],[..]], "D":..., "L":..., "R":..., "F":..., "B":...}}
```
Each of the six faces is a 3x3 grid of single-character colors drawn from `W, Y, R, O, G, B`.
A turn only rotates the stickers of one face and cycles the adjacent strips of its four neighboring
faces; the center sticker of every face never moves.

The saved set includes normally scrambled cubes, one cube that is already solved, one cube whose
color counts are not exactly nine per color, and two cubes that differ from each other by only a
single turn.

## What to build

Provide a program that is invoked as:
```
python solution.py --input /app/inputs --output /app/result.json
```
It reads every puzzle under `<input>/puzzles/`, processes each one independently, and writes a JSON
file to the `--output` path with this exact shape:
```
{"puzzles": [ {"id": ..., "status": ..., "moves": ..., "move_count": ...}, ... ]}
```

For each puzzle emit one record:
- `status = "solved"`: the cube is a legal, scrambled cube. `moves` is the list of move tokens that,
  applied in order to the puzzle's original `faces`, restores it to a fully solved cube.
  `move_count` equals `len(moves)`.
- `status = "already_solved"`: the cube is a legal cube that is already solved (each face uniform).
  `moves = []` and `move_count = 0`.
- `status = "invalid"`: the cube configuration is not a legal cube (for example some color does not
  appear exactly nine times, a face is not a 3x3 grid, or a color is outside `W, Y, R, O, G, B`).
  `moves = []`, `move_count = 0`, and include a non-empty `reason` string describing the problem.

## Requirements
- Every emitted move token MUST be a member of the `tokens` alphabet defined in `notation.json`.
- The `puzzles` array MUST be sorted by `id` in ascending order.
- Each puzzle MUST be processed independently; one puzzle's result must not affect another's.
- Output must be deterministic: running the program twice on the same input yields identical bytes.
