#!/usr/bin/env bash
set -euo pipefail

cat > /app/solution.py <<'PY'
import argparse, json, os
from decimal import Decimal, ROUND_HALF_UP

NEIGHBORS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]


def r1(x):
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def load(path):
    with open(path) as f:
        return json.load(f)


def merge_buildings(base, override):
    merged = {bid: dict(fields) for bid, fields in base.items()}
    for bid, fields in override.items():
        if bid not in merged:
            merged[bid] = dict(fields)
        else:
            for k, v in fields.items():
                merged[bid][k] = v
    return merged


def build_report(gs, terrain, base_b, override, stock):
    buildings = merge_buildings(base_b, override)
    adults = gs.get("population", {}).get("adults", 0)
    tiles = gs.get("tiles", [])

    seen = set()
    authoritative = []
    invalid = []
    for t in tiles:
        q, r = t["q"], t["r"]
        coord = (q, r)
        if coord in seen:
            invalid.append({"q": q, "r": r, "reason": "duplicate_coord"})
            continue
        seen.add(coord)
        terr = t.get("terrain")
        bld = t.get("building")
        if terr not in terrain:
            invalid.append({"q": q, "r": r, "reason": "unknown_terrain"})
        elif bld is not None and bld not in buildings:
            invalid.append({"q": q, "r": r, "reason": "placed_building_unknown"})
        authoritative.append(t)

    bad_coords = {(iv["q"], iv["r"]) for iv in invalid if iv["reason"] != "duplicate_coord"}
    player_coords = {(t["q"], t["r"]) for t in authoritative if t.get("owner") == "player"}

    buildable = []
    for t in authoritative:
        q, r = t["q"], t["r"]
        if (q, r) in bad_coords:
            continue
        if t.get("owner") is not None:
            continue
        if t.get("building") is not None:
            continue
        terr = t.get("terrain")
        tdef = terrain.get(terr)
        if not tdef or not tdef.get("buildable"):
            continue
        if not any((q + dq, r + dr) in player_coords for dq, dr in NEIGHBORS):
            continue
        opts = []
        for bid, bdef in buildings.items():
            if terr not in bdef.get("allowed_terrains", []):
                continue
            cost = bdef.get("cost", {})
            if not all(stock.get(res, 0) >= amt for res, amt in cost.items()):
                continue
            opts.append((sum(cost.values()), bid))
        opts.sort(key=lambda x: (x[0], x[1]))
        buildable.append({"q": q, "r": r, "terrain": terr, "options": [b for _, b in opts]})
    buildable.sort(key=lambda o: (o["q"], o["r"]))

    prod = {}
    for t in authoritative:
        bld = t.get("building")
        if bld is None or bld not in buildings:
            continue
        bdef = buildings[bld]
        res = bdef.get("output_resource")
        req = bdef.get("workers_required", 0)
        asg = bdef.get("workers_assigned", 0)
        base = bdef.get("base_output", 0)
        contrib = 0.0 if req == 0 else base * (asg / req)
        prod[res] = prod.get(res, 0.0) + contrib
    production = {res: r1(v) for res, v in prod.items()}

    food = production.get("food", 0.0)
    net = r1(food - adults)
    if net >= 0:
        surv = "survive"
    elif net >= -adults * 0.25:
        surv = "weak"
    else:
        surv = "collapse"

    invalid.sort(key=lambda o: (o["q"], o["r"]))
    return {
        "buildable_tiles": buildable,
        "production_per_resource": production,
        "net_food_per_turn": net,
        "survival": surv,
        "invalid_tiles": invalid,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    d = args.input
    gs = load(os.path.join(d, "game_state.json"))
    terrain = load(os.path.join(d, "terrain.json"))
    base_b = load(os.path.join(d, "buildings.json"))
    stock = load(os.path.join(d, "stockpile.json"))
    ov_path = os.path.join(d, "buildings_override.json")
    override = load(ov_path) if os.path.exists(ov_path) else {}
    result = build_report(gs, terrain, base_b, override, stock)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
PY

python /app/solution.py --input /app/inputs --output /app/result.json
echo "solved -> /app/result.json"
