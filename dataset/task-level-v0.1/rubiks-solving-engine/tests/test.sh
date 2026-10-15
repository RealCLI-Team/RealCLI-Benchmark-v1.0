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
set -uo pipefail

RESULT="/app/result.json"
INPUT="/app/inputs"

if [ ! -f "$RESULT" ]; then
  echo "FAIL: $RESULT does not exist"
  exit 1
fi

python3 - "$RESULT" "$INPUT" <<'PY'
import sys, json, os, glob
from collections import Counter

result_path, input_dir = sys.argv[1], sys.argv[2]
FACES=["U","D","L","R","F","B"]
COLORS=set("WYROGB")

# ----- independent cube simulator -----
def rot_cw(f):
    return [[f[2-c][r] for c in range(3)] for r in range(3)]
def clone(s):
    return {k:[row[:] for row in s[k]] for k in FACES}
def mU(s):
    s["U"]=rot_cw(s["U"])
    t=s["F"][0][:]; s["F"][0]=s["R"][0][:]; s["R"][0]=s["B"][0][:]; s["B"][0]=s["L"][0][:]; s["L"][0]=t
def mD(s):
    s["D"]=rot_cw(s["D"])
    t=s["F"][2][:]; s["F"][2]=s["L"][2][:]; s["L"][2]=s["B"][2][:]; s["B"][2]=s["R"][2][:]; s["R"][2]=t
def mF(s):
    s["F"]=rot_cw(s["F"])
    t=s["U"][2][:]
    s["U"][2]=[s["L"][2][2],s["L"][1][2],s["L"][0][2]]
    for i in range(3): s["L"][i][2]=s["D"][0][i]
    s["D"][0]=[s["R"][2][0],s["R"][1][0],s["R"][0][0]]
    for i in range(3): s["R"][i][0]=t[i]
def mB(s):
    s["B"]=rot_cw(s["B"])
    t=s["U"][0][:]
    s["U"][0]=[s["R"][0][2],s["R"][1][2],s["R"][2][2]]
    for i in range(3): s["R"][i][2]=s["D"][2][2-i]
    s["D"][2]=[s["L"][0][0],s["L"][1][0],s["L"][2][0]]
    for i in range(3): s["L"][i][0]=t[2-i]
def mL(s):
    s["L"]=rot_cw(s["L"])
    t=[s["U"][i][0] for i in range(3)]
    for i in range(3): s["U"][i][0]=s["B"][2-i][2]
    for i in range(3): s["B"][2-i][2]=s["D"][i][0]
    for i in range(3): s["D"][i][0]=s["F"][i][0]
    for i in range(3): s["F"][i][0]=t[i]
def mR(s):
    s["R"]=rot_cw(s["R"])
    t=[s["U"][i][2] for i in range(3)]
    for i in range(3): s["U"][i][2]=s["F"][i][2]
    for i in range(3): s["F"][i][2]=s["D"][i][2]
    for i in range(3): s["D"][i][2]=s["B"][2-i][0]
    for i in range(3): s["B"][2-i][0]=t[i]
BASE={"U":mU,"D":mD,"L":mL,"R":mR,"F":mF,"B":mB}
def apply(s,tok):
    fn=BASE[tok[0]]; mod=tok[1:]
    if mod=="": fn(s)
    elif mod=="'": fn(s);fn(s);fn(s)
    elif mod=="2": fn(s);fn(s)
    else: raise ValueError(tok)
def uniform(s):
    return all(all(all(x==s[k][1][1] for x in row) for row in s[k]) for k in FACES)

errors=[]
def check(cond,msg):
    if not cond: errors.append(msg)

# load notation alphabet
with open(os.path.join(input_dir,"notation.json")) as f:
    notation=json.load(f)
alphabet=set(notation["tokens"])

# load original puzzles
orig={}
for p in glob.glob(os.path.join(input_dir,"puzzles","*.json")):
    o=json.load(open(p))
    orig[o["id"]]=o["faces"]

data=json.load(open(result_path))
check(isinstance(data,dict) and "puzzles" in data, "result.json must have top-level 'puzzles'")
puzzles=data.get("puzzles",[])
check(isinstance(puzzles,list), "'puzzles' must be a list")

by_id={r["id"]:r for r in puzzles if isinstance(r,dict) and "id" in r}

# sorted ascending
ids=[r["id"] for r in puzzles]
check(ids==sorted(ids), f"puzzles not sorted ascending by id: {ids}")

# every puzzle file represented exactly once
check(sorted(by_id.keys())==sorted(orig.keys()), "result must contain every input puzzle exactly once")

# per-record structural + move-token checks
for r in puzzles:
    pid=r.get("id")
    st=r.get("status")
    mv=r.get("moves")
    mc=r.get("move_count")
    check(st in ("solved","already_solved","invalid"), f"{pid}: bad status {st}")
    check(isinstance(mv,list), f"{pid}: moves must be a list")
    if isinstance(mv,list):
        check(mc==len(mv), f"{pid}: move_count {mc} != len(moves) {len(mv)}")
        for t in mv:
            check(t in alphabet, f"{pid}: illegal move token {t!r}")

def replay_solved(pid,r):
    s={k:[row[:] for row in orig[pid][k]] for k in FACES}
    for t in r["moves"]:
        apply(s,t)
    return uniform(s)

# solved cubes must replay to a uniform cube
for pid,r in by_id.items():
    if r.get("status")=="solved":
        check(len(r.get("moves",[]))>0, f"{pid}: solved status but empty moves")
        check(replay_solved(pid,r), f"{pid}: replaying moves does not yield a uniform solved cube")

# already-solved cube
r=by_id.get("cube-solved")
check(r is not None and r.get("status")=="already_solved", "cube-solved must be already_solved")
if r: check(r.get("moves")==[] and r.get("move_count")==0, "cube-solved must have empty moves")

# invalid cube
r=by_id.get("cube-broken")
check(r is not None and r.get("status")=="invalid", "cube-broken must be invalid")
if r:
    check(isinstance(r.get("reason"),str) and len(r.get("reason",""))>0, "cube-broken must include non-empty reason")
    check(r.get("moves")==[] and r.get("move_count")==0, "cube-broken must have empty moves")

# near-identical pair: independent valid solutions
for pid in ("cube-near-a","cube-near-b"):
    r=by_id.get(pid)
    check(r is not None and r.get("status")=="solved", f"{pid} must be solved")
    if r and r.get("status")=="solved":
        check(replay_solved(pid,r), f"{pid}: its own moves must replay to solved (no state leakage)")
# cross-check: applying near-a's solution to near-b must NOT (in general) solve it, proving independence
ra=by_id.get("cube-near-a"); rb=by_id.get("cube-near-b")
if ra and rb and ra.get("status")=="solved" and rb.get("status")=="solved":
    s={k:[row[:] for row in orig["cube-near-b"][k]] for k in FACES}
    for t in ra["moves"]:
        apply(s,t)
    check(not uniform(s), "cube-near-b solved by cube-near-a's moves => solution reuse / state leakage")

# scrambled cubes present
check(by_id.get("cube-alpha",{}).get("status")=="solved", "cube-alpha must be solved")
check(by_id.get("cube-delta",{}).get("status")=="solved", "cube-delta must be solved")

if errors:
    print("TEST FAILURES:")
    for e in errors: print(" -",e)
    sys.exit(1)
print("content checks passed")
PY
rc=$?
if [ $rc -ne 0 ]; then exit $rc; fi

# determinism: re-run the produced solver and compare bytes
if [ ! -f /app/solution.py ]; then
  echo "FAIL: /app/solution.py not found for determinism check"
  exit 1
fi
python3 /app/solution.py --input /app/inputs --output /tmp/result_rerun.json
if ! cmp -s /app/result.json /tmp/result_rerun.json; then
  echo "FAIL: result.json is not byte-stable across runs"
  exit 1
fi

echo "ALL TESTS PASSED"
