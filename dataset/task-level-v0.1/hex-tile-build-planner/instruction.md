# Hex-tile build planner & production report

A turn-based survival colony game stores its board as a flat-top **axial hex grid**. Each turn the
UI needs a machine-readable planning report derived from a read-only state snapshot plus config:
which empty tiles the player could build on, what each such tile could build, the current
per-resource production and the resulting food balance, and which map tiles are unusable because of
bad data.

Write a program `solution.py` that reads the input package and writes `result.json`. It must be
invoked exactly as:

```
python solution.py --input /app/inputs --output /app/result.json
```

Reading the input must **not** modify any input file.

## Input package (`/app/inputs/`)

- `game_state.json`
  - `population.adults`: integer ≥ 0. Each adult consumes exactly `1` food per turn.
  - `tiles`: array of tiles. Each tile has `q` (int), `r` (int), `terrain` (string),
    `owner` (faction id string **or** `null`), `building` (building id string **or** `null`).
- `terrain.json`: object mapping `terrain_id -> { "buildable": bool }`.
- `buildings.json`: object mapping `building_id -> { "allowed_terrains": [str],
  "cost": { resource: int }, "workers_required": int, "workers_assigned": int,
  "output_resource": str, "base_output": number }`.
- `buildings_override.json`: object with the **same shape** as `buildings.json` but only some
  fields present per building. It patches `buildings.json` at the **field level**: for any building
  id, a field present in the override replaces that field; fields absent in the override keep their
  base value. A building id that appears only in the override is added. This file may be absent.
- `stockpile.json`: object mapping `resource -> amount` (int ≥ 0). This is the player faction's
  available resources.

The player faction id is the string `"player"`. The six axial neighbor offsets are
`(+1,0), (+1,-1), (0,-1), (-1,0), (-1,+1), (0,+1)`.

## Required behavior

First, merge `buildings_override.json` into `buildings.json` at the field level to obtain the
**merged building definitions** used everywhere below.

Process `tiles` in order, tracking already-seen `(q,r)` coordinates. The **first** occurrence of a
coordinate is authoritative for all computations; any later tile repeating that coordinate is a
duplicate and is only reported in `invalid_tiles`.

Write `result.json` (JSON, 2-space indented) with exactly these top-level keys:

1. `buildable_tiles`: array of objects `{ "q", "r", "terrain", "options": [building_id, ...] }`.
   An authoritative tile qualifies as a **candidate** when all of the following hold:
   `owner` is `null`, `building` is `null`, its `terrain` exists in `terrain.json` with
   `buildable == true`, and at least one of its six axial neighbors is a tile owned by `"player"`.
   For a candidate tile, a building id is an **option** when the tile terrain is in the building's
   `allowed_terrains` **and** the stockpile can afford every entry of the building's `cost`
   (stockpile amount ≥ required amount for each cost resource; a resource absent from the stockpile
   counts as `0`). `options` is sorted by total cost ascending (sum of all values in `cost`), ties
   broken by building id ascending. A candidate tile with zero options is still listed with an empty
   `options` array. `buildable_tiles` is sorted by `q` ascending, then `r` ascending. Tiles reported
   in `invalid_tiles` are excluded from `buildable_tiles`.
2. `production_per_resource`: object mapping `output_resource -> total`, summed over every
   authoritative tile whose `building` is a **known** merged building. Per such tile the
   contribution is `base_output * (workers_assigned / workers_required)` using the merged building
   definition; if `workers_required` is `0` the contribution is `0`. Each resulting total is rounded
   to 1 decimal place using round-half-up. The set of keys is exactly the `output_resource` values
   of the placed known buildings; a resource is still included with value `0.0` if a placed known
   building targets it. Do not invent resources.
3. `net_food_per_turn`: `production_per_resource["food"]` (or `0.0` if that key is absent) minus
   `population.adults`, rounded to 1 decimal place using round-half-up.
4. `survival`: `"survive"` if `net_food_per_turn >= 0`; otherwise `"weak"` if
   `net_food_per_turn >= -population.adults * 0.25`; otherwise `"collapse"`.
5. `invalid_tiles`: array of `{ "q", "r", "reason" }`, sorted by `q` ascending then `r` ascending.
   Emit exactly one entry per offending tile using the first applicable reason in this order:
   - `"duplicate_coord"`: this tile repeats a `(q,r)` already seen earlier in `tiles`.
   - `"unknown_terrain"`: `terrain` is not present in `terrain.json`.
   - `"placed_building_unknown"`: `building` is non-null but not a known merged building.
   An `owner` of `null` is a normal unowned tile and is never invalid on its own.

## Example

Given the provided package under `/app/inputs`, `result.json` contains
`buildable_tiles` in the order `(0,1)`, `(1,-1)`, `(1,0)`; the tile `(0,1)` has options
`["lumber_camp"]`, `(1,-1)` has options `[]`, and `(1,0)` has options `["farm"]`.
`production_per_resource` is `{"food": 5.0, "wood": 6.0}`, `net_food_per_turn` is `-1.0`,
`survival` is `"weak"`, and `invalid_tiles` is `[]`.
