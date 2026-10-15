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

RESULT="/app/result.json"
INPUT_DIR="/app/inputs/data"

if [ ! -f "$RESULT" ]; then
    echo "FAIL: $RESULT not found"; exit 1
fi

python3 - "$RESULT" "$INPUT_DIR" << 'PY'
import json, sys, os, random
from collections import Counter

result_path, input_dir = sys.argv[1], sys.argv[2]

# ---- Independent reference cube engine (not shared with the solver) ----
def build_moves():
    coords = {}
    def setface(base, normal, fn):
        for r in range(3):
            for c in range(3):
                coords[base + r*3 + c] = (fn(r, c), normal)
    setface(0,  (0, 1, 0),  lambda r, c: (c-1, 1, r-1))
    setface(9,  (1, 0, 0),  lambda r, c: (1, 1-r, 1-c))
    setface(18, (0, 0, 1),  lambda r, c: (c-1, 1-r, 1))
    setface(27, (0, -1, 0), lambda r, c: (c-1, -1, 1-r))
    setface(36, (-1, 0, 0), lambda r, c: (-1, 1-r, c-1))
    setface(45, (0, 0, -1), lambda r, c: (1-c, 1-r, -1))
    pos2idx = {(cc, nn): i for i, (cc, nn) in coords.items()}
    def rot(axis, v):
        x, y, z = v
        if axis == 'x': return (x, -z, y)
        if axis == 'y': return (z, y, -x)
        return (-y, x, z)
    spec = {'U': ('y', 1), 'D': ('y', -1), 'R': ('x', 1),
            'L': ('x', -1), 'F': ('z', 1), 'B': ('z', -1)}
    ai = {'x': 0, 'y': 1, 'z': 2}
    mv = {}
    for m, (axis, val) in spec.items():
        perm = list(range(54)); k = ai[axis]
        for i, (cc, nn) in coords.items():
            if cc[k] == val:
                perm[pos2idx[(rot(axis, cc), rot(axis, nn))]] = i
        mv[m] = perm
    return mv

REF = build_moves()
SOLVED = "UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB"
LEGAL = set(b + s for b in "URFDLB" for s in ["", "'", "2"])

def ref_apply(state, token):
    perm = REF[token[0]]
    t = 1 if len(token) == 1 else (3 if token[1] == "'" else 2)
    for _ in range(t):
        state = ''.join(state[perm[i]] for i in range(54))
    return state

def ref_seq(state, seq):
    for tok in seq:
        state = ref_apply(state, tok)
    return state

def expected_status(state, is_dup):
    if is_dup:
        return "invalid"
    if not isinstance(state, str) or len(state) != 54:
        return "invalid"
    cnt = Counter(state)
    if any(k not in "URFDLB" for k in cnt) or any(cnt.get(c, 0) != 9 for c in "URFDLB"):
        return "invalid"
    return "already_solved" if state == SOLVED else "solved"

def fail(msg):
    print("FAIL: " + msg); sys.exit(1)

# ---- load input package ----
order, states, seen, dup = [], {}, set(), set()
with open(os.path.join(input_dir, "cubes.jsonl")) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        rid = r["id"]
        if rid in seen:
            dup.add(rid)
        else:
            seen.add(rid); order.append(rid)
        states[rid] = r.get("state")

with open(result_path) as f:
    data = json.load(f)
if "results" not in data or not isinstance(data["results"], list):
    fail("result.json must have a top-level 'results' list")
results = data["results"]

ids = [r["id"] for r in results]
if ids != sorted(ids):
    fail("results are not sorted by id ascending")
if len(ids) != len(set(ids)):
    fail("duplicate id appears more than once in results")
if set(ids) != set(order):
    fail("result ids do not match unique input ids: %r vs %r" % (set(ids), set(order)))

by_id = {r["id"]: r for r in results}

solved_seen = already_seen = invalid_seen = 0
for rid in order:
    rec = by_id[rid]
    exp = expected_status(states[rid], rid in dup)
    st = rec.get("status")
    if st != exp:
        fail("id %s expected status %s got %s" % (rid, exp, st))
    if st == "invalid":
        invalid_seen += 1
        if rec.get("moves") is not None:
            fail("id %s invalid must have moves null" % rid)
        if rec.get("move_count") != 0:
            fail("id %s invalid must have move_count 0" % rid)
    elif st == "already_solved":
        already_seen += 1
        if rec.get("moves") != []:
            fail("id %s already_solved must have empty moves list" % rid)
        if rec.get("move_count") != 0:
            fail("id %s already_solved must have move_count 0" % rid)
        if states[rid] != SOLVED:
            fail("id %s marked already_solved but state != solved" % rid)
    elif st == "solved":
        solved_seen += 1
        mv = rec.get("moves")
        if not isinstance(mv, list) or len(mv) == 0:
            fail("id %s solved must have non-empty moves list" % rid)
        for tok in mv:
            if tok not in LEGAL:
                fail("id %s emitted illegal move token %r" % (rid, tok))
        if rec.get("move_count") != len(mv):
            fail("id %s move_count != len(moves)" % rid)
        if ref_seq(states[rid], mv) != SOLVED:
            fail("id %s moves do not restore its own state to solved" % rid)
    else:
        fail("id %s has unknown status %s" % (rid, st))

if solved_seen < 1 or already_seen < 1 or invalid_seen < 1:
    fail("fixture must exercise solved, already_solved and invalid outcomes")

# ---- near-duplicate independence: cube-04 and cube-05 differ and are solved on their own ----
if "cube-04" in by_id and "cube-05" in by_id:
    if states["cube-04"] == states["cube-05"]:
        fail("cube-04 and cube-05 should have different states")
    m4 = by_id["cube-04"]["moves"]
    if isinstance(m4, list) and ref_seq(states["cube-05"], m4) == SOLVED:
        fail("cube-05 must not be solved by cube-04's move sequence")

# ---- generalization: fresh scramble with a new seed, solved by the same CLI ----
rng = random.Random(4242)
def scramble(depth):
    seq, prev = [], None
    toks = sorted(LEGAL)
    for _ in range(depth):
        while True:
            t = rng.choice(toks)
            if t[0] != prev:
                break
        prev = t[0]; seq.append(t)
    return ref_seq(SOLVED, seq)

tmp = "/tmp/gen_case"
os.makedirs(tmp, exist_ok=True)
gen = [("g-1", scramble(6)), ("g-2", scramble(4)), ("g-3", SOLVED),
       ("g-4", scramble(7))]
with open(os.path.join(tmp, "cubes.jsonl"), "w") as f:
    for rid, stt in gen:
        f.write(json.dumps({"id": rid, "state": stt}) + "\n")
with open(os.path.join(tmp, "notation.json"), "w") as f:
    json.dump({"solved_state": SOLVED}, f)

rc = os.system("python3 /app/solution.py --input %s --output %s/out.json" % (tmp, tmp))
if rc != 0:
    fail("solution.py failed on freshly generated cases")
with open(os.path.join(tmp, "out.json")) as f:
    g = json.load(f)["results"]
gmap = {r["id"]: r for r in g}
if [r["id"] for r in g] != sorted(r["id"] for r in g):
    fail("generalization output not sorted by id")
for rid, stt in gen:
    r = gmap[rid]
    if stt == SOLVED:
        if r["status"] != "already_solved" or r["moves"] != []:
            fail("generalization: %s should be already_solved" % rid)
    else:
        if r["status"] != "solved":
            fail("generalization: %s should be solved" % rid)
        for tok in r["moves"]:
            if tok not in LEGAL:
                fail("generalization: %s illegal token %r" % (rid, tok))
        if r["move_count"] != len(r["moves"]):
            fail("generalization: %s move_count mismatch" % rid)
        if ref_seq(stt, r["moves"]) != SOLVED:
            fail("generalization: %s moves do not restore solved state" % rid)

print("PASS")
PY
