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

python3 - << 'PY'
import json, os, subprocess, sys, tempfile, random

RESULT = "/app/result.json"
INPUT = "/app/inputs/data"
SOL = "/app/solution.py"

fails = []
def check(cond, msg):
    if not cond:
        fails.append(msg)

# ---- independent reference simulator (built from the documented cycles) ----
SOLVED = "U"*9 + "R"*9 + "F"*9 + "D"*9 + "L"*9 + "B"*9
CYCLES = {
    "U": [(0,6,8,2),(1,3,7,5),(9,45,36,18),(10,46,37,19),(11,47,38,20)],
    "R": [(2,20,29,51),(5,23,32,48),(8,26,35,45),(9,15,17,11),(10,12,16,14)],
    "F": [(6,44,29,9),(7,41,28,12),(8,38,27,15),(18,24,26,20),(19,21,25,23)],
    "D": [(15,51,42,24),(16,52,43,25),(17,53,44,26),(27,29,35,33),(28,32,34,30)],
    "L": [(0,18,27,53),(3,21,30,50),(6,24,33,47),(36,38,44,42),(37,41,43,39)],
    "B": [(0,42,35,11),(1,39,34,14),(2,36,33,17),(45,47,53,51),(46,50,52,48)],
}
def qturn(state, f):
    new = list(state)
    for a,b,c,d in CYCLES[f]:
        new[b]=state[a]; new[c]=state[b]; new[d]=state[c]; new[a]=state[d]
    return new
def ref_apply(state, move):
    t = {"":1,"'":3,"2":2}[move[1:]]
    s = list(state)
    for _ in range(t):
        s = qturn(s, move[0])
    return s
def ref_seq(state, seq):
    s = list(state)
    for m in seq:
        s = ref_apply(s, m)
    return "".join(s)

import re
MOVE_RE = re.compile(r"^[URFDLB](2|')?$")

def load_result(path):
    with open(path) as fh:
        return json.load(fh)

if not os.path.exists(RESULT):
    print("MISSING /app/result.json", file=sys.stderr); sys.exit(1)

res = load_result(RESULT)

# top-level keys sorted ascending
keys = list(res.keys())
check(keys == sorted(keys), "top-level case_id keys not in ascending order")

def load_case(cid):
    with open(os.path.join(INPUT, cid + ".json")) as fh:
        return json.load(fh)

def assert_solved(cid):
    check(cid in res, f"{cid} missing from report")
    if cid not in res: return
    r = res[cid]
    check(r.get("status") == "solved", f"{cid} status != solved: {r.get('status')}")
    sol = r.get("solution")
    check(isinstance(sol, list), f"{cid} solution not a list")
    if not isinstance(sol, list): return
    check(all(isinstance(m,str) and MOVE_RE.match(m) for m in sol),
          f"{cid} solution has illegal tokens")
    check(r.get("move_count") == len(sol), f"{cid} move_count mismatch")
    # canonical: no adjacent same-face pair
    for i in range(1, len(sol)):
        check(sol[i][0] != sol[i-1][0],
              f"{cid} solution has adjacent same-face pair at {i-1},{i}")
    # replay restores solved
    cube = load_case(cid)["cube"]
    check(ref_seq(cube, sol) == SOLVED, f"{cid} solution does not restore cube")

def assert_status(cid, expected):
    check(cid in res, f"{cid} missing from report")
    if cid not in res: return
    r = res[cid]
    check(r.get("status") == expected, f"{cid} status expected {expected} got {r.get('status')}")
    if expected == "already_solved":
        check(r.get("solution") == [], f"{cid} already_solved solution not empty")
        check(r.get("move_count") == 0, f"{cid} already_solved move_count != 0")
    if expected == "invalid":
        check("solution" not in r, f"{cid} invalid must not carry solution")

# ---- fixture expectations ----
assert_status("case_01_solved_plain", "already_solved")
assert_solved("case_02_scramble_consistent")
assert_status("case_03_scramble_conflict", "invalid")
assert_status("case_04_bad_length", "invalid")
assert_status("case_05_bad_char", "invalid")
assert_status("case_06_bad_counts", "invalid")
assert_status("case_07_bad_scramble_token", "invalid")
assert_status("case_08_scramble_cancels", "already_solved")
assert_solved("case_09_single_turn")

# ---- determinism: run twice, byte-identical ----
with tempfile.TemporaryDirectory() as td:
    o1 = os.path.join(td, "r1.json"); o2 = os.path.join(td, "r2.json")
    subprocess.run(["python3", SOL, "--input", INPUT, "--output", o1], check=True)
    subprocess.run(["python3", SOL, "--input", INPUT, "--output", o2], check=True)
    check(open(o1,"rb").read() == open(o2,"rb").read(), "output not deterministic across runs")

# ---- seeded generated case: random scramble, replay-verify solution ----
rng = random.Random(20260812)
faces = "URFDLB"; sufs = ["", "'", "2"]
with tempfile.TemporaryDirectory() as gd, tempfile.TemporaryDirectory() as od:
    prev = None
    scramble = []
    for _ in range(9):
        f = rng.choice(faces)
        while f == prev:
            f = rng.choice(faces)
        prev = f
        scramble.append(f + rng.choice(sufs))
    gcube = ref_seq(SOLVED, scramble)
    check(gcube != SOLVED, "generated scramble unexpectedly solved")
    # inconsistent case: alter one facelet so color counts break -> invalid
    bad = list(gcube)
    bad[0] = "U" if bad[0] != "U" else "R"
    with open(os.path.join(gd, "gen_case.json"), "w") as fh:
        json.dump({"case_id": "gen_case", "cube": gcube, "scramble": scramble}, fh)
    with open(os.path.join(gd, "gen_bad.json"), "w") as fh:
        json.dump({"case_id": "gen_bad", "cube": "".join(bad), "scramble": scramble}, fh)
    out = os.path.join(od, "out.json")
    subprocess.run(["python3", SOL, "--input", gd, "--output", out], check=True)
    gres = load_result(out)
    check("gen_case" in gres, "generated case missing")
    if "gen_case" in gres:
        gr = gres["gen_case"]
        check(gr.get("status") == "solved", f"gen_case status {gr.get('status')}")
        gsol = gr.get("solution", [])
        for i in range(1, len(gsol)):
            check(gsol[i][0] != gsol[i-1][0], f"gen_case adjacent same-face at {i}")
        check(ref_seq(gcube, gsol) == SOLVED, "gen_case solution does not restore cube")
        check(gr.get("move_count") == len(gsol), "gen_case move_count mismatch")
    check(gres.get("gen_bad", {}).get("status") == "invalid",
          "gen_bad expected invalid")

if fails:
    print("TEST FAILURES:", file=sys.stderr)
    for m in fails:
        print(" -", m, file=sys.stderr)
    sys.exit(1)
print("ALL TESTS PASSED")
PY
