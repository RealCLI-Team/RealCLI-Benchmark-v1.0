#!/usr/bin/env bash
# HARBOR_REWARD_WRAPPER_V02
set +e
mkdir -p /logs/verifier
_harbor_inner_script_v02="$(mktemp)"
sed '1,/^__HARBOR_ORIGINAL_SCRIPT_V02__$/d' "$0" > "$_harbor_inner_script_v02"
bash "$_harbor_inner_script_v02"
_harbor_rc_v02=$?
rm -f "$_harbor_inner_script_v02"
if [ "$_harbor_rc_v02" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit 0
__HARBOR_ORIGINAL_SCRIPT_V02__
set -euo pipefail

SOL=/app/solution.py
if [ ! -f "$SOL" ]; then
  echo "FAIL: /app/solution.py not found (solution has not been produced)"
  exit 1
fi

python3 <<'PY'
import json, os, subprocess, sys, tempfile, hashlib, random
from decimal import Decimal, ROUND_HALF_UP

SOL = "/app/solution.py"
NEIGHBORS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
FAILS = []

def check(cond, msg):
    if not cond:
        FAILS.append(msg)

def r1(x):
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))

# ---- independent reference implementation (mirrors the business contract) ----
def merge(base, ov):
    m = {b: dict(f) for b, f in base.items()}
    for b, f in ov.items():
        if b not in m:
            m[b] = dict(f)
        else:
            for k, v in f.items():
                m[b][k] = v
    return m

def reference(gs, terrain, base_b, ov, stock):
    B = merge(base_b, ov)
    adults = gs.get("population", {}).get("adults", 0)
    seen = set(); auth = []; invalid = []
    for t in gs.get("tiles", []):
        q, r = t["q"], t["r"]
        if (q, r) in seen:
            invalid.append({"q": q, "r": r, "reason": "duplicate_coord"}); continue
        seen.add((q, r))
        terr = t.get("terrain"); bld = t.get("building")
        if terr not in terrain:
            invalid.append({"q": q, "r": r, "reason": "unknown_terrain"})
        elif bld is not None and bld not in B:
            invalid.append({"q": q, "r": r, "reason": "placed_building_unknown"})
        auth.append(t)
    bad = {(i["q"], i["r"]) for i in invalid if i["reason"] != "duplicate_coord"}
    players = {(t["q"], t["r"]) for t in auth if t.get("owner") == "player"}
    buildable = []
    for t in auth:
        q, r = t["q"], t["r"]
        if (q, r) in bad: continue
        if t.get("owner") is not None: continue
        if t.get("building") is not None: continue
        terr = t.get("terrain"); td = terrain.get(terr)
        if not td or not td.get("buildable"): continue
        if not any((q + dq, r + dr) in players for dq, dr in NEIGHBORS): continue
        opts = []
        for bid, bd in B.items():
            if terr not in bd.get("allowed_terrains", []): continue
            cost = bd.get("cost", {})
            if not all(stock.get(res, 0) >= amt for res, amt in cost.items()): continue
            opts.append((sum(cost.values()), bid))
        opts.sort(key=lambda x: (x[0], x[1]))
        buildable.append({"q": q, "r": r, "terrain": terr, "options": [b for _, b in opts]})
    buildable.sort(key=lambda o: (o["q"], o["r"]))
    prod = {}
    for t in auth:
        bld = t.get("building")
        if bld is None or bld not in B: continue
        bd = B[bld]; res = bd.get("output_resource")
        req = bd.get("workers_required", 0); asg = bd.get("workers_assigned", 0)
        base = bd.get("base_output", 0)
        prod[res] = prod.get(res, 0.0) + (0.0 if req == 0 else base * (asg / req))
    production = {res: r1(v) for res, v in prod.items()}
    food = production.get("food", 0.0)
    net = r1(food - adults)
    if net >= 0: surv = "survive"
    elif net >= -adults * 0.25: surv = "weak"
    else: surv = "collapse"
    invalid.sort(key=lambda o: (o["q"], o["r"]))
    return {"buildable_tiles": buildable, "production_per_resource": production,
            "net_food_per_turn": net, "survival": surv, "invalid_tiles": invalid}

def write_case(d, gs, terrain, buildings, stock, override=None):
    os.makedirs(d, exist_ok=True)
    json.dump(gs, open(os.path.join(d, "game_state.json"), "w"))
    json.dump(terrain, open(os.path.join(d, "terrain.json"), "w"))
    json.dump(buildings, open(os.path.join(d, "buildings.json"), "w"))
    json.dump(stock, open(os.path.join(d, "stockpile.json"), "w"))
    if override is not None:
        json.dump(override, open(os.path.join(d, "buildings_override.json"), "w"))

def run(d):
    out = os.path.join(d, "out.json")
    subprocess.run([sys.executable, SOL, "--input", d, "--output", out], check=True)
    return json.load(open(out))

tmp = tempfile.mkdtemp()

# =========================== CASE A: main provided package ===========================
res = run("/app/inputs")
bt = res["buildable_tiles"]
check([(x["q"], x["r"]) for x in bt] == [(0, 1), (1, -1), (1, 0)],
      "A: buildable_tiles order wrong: %r" % [(x["q"], x["r"]) for x in bt])
opt_map = {(x["q"], x["r"]): x["options"] for x in bt}
check(opt_map.get((0, 1)) == ["lumber_camp"], "A: (0,1) options %r" % opt_map.get((0, 1)))
check(opt_map.get((1, -1)) == [], "A: (1,-1) options should be empty %r" % opt_map.get((1, -1)))
check(opt_map.get((1, 0)) == ["farm"], "A: (1,0) options %r" % opt_map.get((1, 0)))
check(res["production_per_resource"] == {"food": 5.0, "wood": 6.0},
      "A: production %r" % res["production_per_resource"])
check(res["net_food_per_turn"] == -1.0, "A: net_food %r" % res["net_food_per_turn"])
check(res["survival"] == "weak", "A: survival %r" % res["survival"])
check(res["invalid_tiles"] == [], "A: invalid_tiles %r" % res["invalid_tiles"])

# read-only contract: input bytes must be unchanged after running
gh_before = hashlib.md5(open("/app/inputs/game_state.json", "rb").read()).hexdigest()
run("/app/inputs")
gh_after = hashlib.md5(open("/app/inputs/game_state.json", "rb").read()).hexdigest()
check(gh_before == gh_after, "A: game_state.json was modified by the solution")

# =========================== CASE B: field-level override precedence ===========================
gsB = {"population": {"adults": 4}, "tiles": [
    {"q": 0, "r": 0, "terrain": "plains", "owner": "player", "building": None},
    {"q": 1, "r": 0, "terrain": "plains", "owner": None, "building": None},
    {"q": 0, "r": 1, "terrain": "plains", "owner": "player", "building": "farm"},
]}
terB = {"plains": {"buildable": True}}
bB = {"farm": {"allowed_terrains": ["plains"], "cost": {"wood": 5},
               "workers_required": 4, "workers_assigned": 2,
               "output_resource": "food", "base_output": 10.0}}
stB = {"wood": 10, "food": 3}

# without override
dB0 = os.path.join(tmp, "B0"); write_case(dB0, gsB, terB, bB, stB, {})
rB0 = run(dB0)
check({(x["q"], x["r"]): x["options"] for x in rB0["buildable_tiles"]}.get((1, 0)) == ["farm"],
      "B0: (1,0) should offer farm")
check(rB0["production_per_resource"] == {"food": 5.0}, "B0: production %r" % rB0["production_per_resource"])
check(rB0["net_food_per_turn"] == 1.0, "B0: net %r" % rB0["net_food_per_turn"])

# with override: add cost.food (flips affordability off) and raise base_output
ovB = {"farm": {"cost": {"wood": 5, "food": 100}, "base_output": 20.0}}
dB1 = os.path.join(tmp, "B1"); write_case(dB1, gsB, terB, bB, stB, ovB)
rB1 = run(dB1)
check({(x["q"], x["r"]): x["options"] for x in rB1["buildable_tiles"]}.get((1, 0)) == [],
      "B1: override cost.food should remove farm option")
check(rB1["production_per_resource"] == {"food": 10.0},
      "B1: override base_output should change production %r" % rB1["production_per_resource"])
check(rB1["net_food_per_turn"] == 6.0, "B1: net after override %r" % rB1["net_food_per_turn"])

# =========================== CASE C: invalid data streaming ===========================
gsC = {"population": {"adults": 0}, "tiles": [
    {"q": 0, "r": 0, "terrain": "plains", "owner": "player", "building": None},
    {"q": 1, "r": 0, "terrain": "badland", "owner": None, "building": None},
    {"q": 2, "r": 0, "terrain": "plains", "owner": None, "building": "castle"},
    {"q": 0, "r": 1, "terrain": "plains", "owner": None, "building": None},
    {"q": 0, "r": 1, "terrain": "forest", "owner": None, "building": None},
]}
terC = {"plains": {"buildable": True}, "water": {"buildable": False}}
bC = {"farm": {"allowed_terrains": ["plains"], "cost": {"wood": 1},
               "workers_required": 2, "workers_assigned": 1,
               "output_resource": "food", "base_output": 4.0}}
stC = {"wood": 5}
dC = os.path.join(tmp, "C"); write_case(dC, gsC, terC, bC, stC, {})
rC = run(dC)
check(rC["invalid_tiles"] == [
        {"q": 0, "r": 1, "reason": "duplicate_coord"},
        {"q": 1, "r": 0, "reason": "unknown_terrain"},
        {"q": 2, "r": 0, "reason": "placed_building_unknown"},
      ], "C: invalid_tiles %r" % rC["invalid_tiles"])
# (0,1) first occurrence is a valid plains candidate; unknown/placed tiles excluded
check([(x["q"], x["r"]) for x in rC["buildable_tiles"]] == [(0, 1)],
      "C: buildable %r" % [(x["q"], x["r"]) for x in rC["buildable_tiles"]])
check({(x["q"], x["r"]): x["options"] for x in rC["buildable_tiles"]}.get((0, 1)) == ["farm"],
      "C: (0,1) options")
check(rC["production_per_resource"] == {}, "C: production should be empty %r" % rC["production_per_resource"])
check(rC["survival"] == "survive", "C: survival %r" % rC["survival"])

# approximate-but-distinct: shared prefix building ids must not merge/confuse
gsD = {"population": {"adults": 2}, "tiles": [
    {"q": 0, "r": 0, "terrain": "plains", "owner": "player", "building": None},
    {"q": 1, "r": 0, "terrain": "plains", "owner": None, "building": None},
    # adjacent only to a null-owner tile, not to any player tile -> NOT a candidate
    {"q": 9, "r": 9, "terrain": "plains", "owner": None, "building": None},
    {"q": 9, "r": 10, "terrain": "plains", "owner": None, "building": None},
]}
terD = {"plains": {"buildable": True}, "swamp": {"buildable": True}}
bD = {"farm": {"allowed_terrains": ["plains"], "cost": {"wood": 3},
               "workers_required": 1, "workers_assigned": 1, "output_resource": "food", "base_output": 5.0},
      "farm_hydro": {"allowed_terrains": ["swamp"], "cost": {"wood": 1},
               "workers_required": 1, "workers_assigned": 1, "output_resource": "food", "base_output": 9.0}}
stD = {"wood": 5}
dD = os.path.join(tmp, "D"); write_case(dD, gsD, terD, bD, stD, {})
rD = run(dD)
dm = {(x["q"], x["r"]): x["options"] for x in rD["buildable_tiles"]}
check(dm.get((1, 0)) == ["farm"], "D: plains tile must only offer farm not farm_hydro %r" % dm.get((1, 0)))
check((9, 9) not in dm and (9, 10) not in dm, "D: tiles not adjacent to player must not be candidates")

# =========================== generated random cases vs reference ===========================
random.seed(1234)
terrains_pool = ["plains", "forest", "mountain", "water", "swamp"]
res_pool = ["wood", "metal", "food", "stone"]
for ci in range(6):
    terrain = {t: {"buildable": random.random() > 0.3} for t in terrains_pool}
    # a couple of unknown terrains sometimes referenced by tiles
    nb = random.randint(1, 4)
    buildings = {}
    for bi in range(nb):
        bid = random.choice(["farm", "farm_hydro", "mine", "camp", "camp_big"]) + "_%d" % bi
        buildings[bid] = {
            "allowed_terrains": random.sample(terrains_pool, k=random.randint(1, 2)),
            "cost": {random.choice(res_pool): random.randint(1, 8) for _ in range(random.randint(1, 3))},
            "workers_required": random.choice([0, 1, 2, 3, 4]),
            "workers_assigned": random.randint(0, 4),
            "output_resource": random.choice(res_pool),
            "base_output": round(random.uniform(1.0, 20.0), 1),
        }
    bids = list(buildings.keys())
    ntiles = random.randint(4, 10)
    tiles = []
    for ti in range(ntiles):
        q = random.randint(-2, 2); r = random.randint(-2, 2)
        terr = random.choice(terrains_pool + ["unknownX"])
        owner = random.choice(["player", None, "enemy"])
        bld = random.choice([None] + bids + ["ghost"])
        tiles.append({"q": q, "r": r, "terrain": terr, "owner": owner, "building": bld})
    gs = {"population": {"adults": random.randint(0, 10)}, "tiles": tiles}
    stock = {res: random.randint(0, 12) for res in res_pool}
    ov = {}
    if random.random() > 0.5 and bids:
        tgt = random.choice(bids)
        ov = {tgt: {"base_output": round(random.uniform(1.0, 25.0), 1)}}
    d = os.path.join(tmp, "R%d" % ci)
    write_case(d, gs, terrain, buildings, stock, ov)
    got = run(d)
    exp = reference(gs, terrain, buildings, ov, stock)
    check(got == exp, "R%d: mismatch with reference\n  got=%r\n  exp=%r" % (ci, got, exp))

if FAILS:
    print("TESTS FAILED (%d):" % len(FAILS))
    for f in FAILS:
        print(" - " + f)
    sys.exit(1)
print("ALL TESTS PASSED")
PY
