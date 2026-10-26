#!/usr/bin/env bash
set -euo pipefail
cd /app

if [ ! -f /app/result.json ]; then
  echo "FAIL: /app/result.json not found"
  exit 1
fi

python3 - <<'PY'
import json, os, sys

FACES = ['U', 'R', 'F', 'D', 'L', 'B']
BASE = {'U': 0, 'R': 9, 'F': 18, 'D': 27, 'L': 36, 'B': 45}
SOLVED = ''.join(ch * 9 for ch in "URFDLB")


def _idx(f, r, c):
    return BASE[f] + r * 3 + c


def _sticker():
    st = {}
    for r in range(3):
        for c in range(3):
            st[_idx('U', r, c)] = ((c - 1, 1, r - 1), (0, 1, 0))
            st[_idx('F', r, c)] = ((c - 1, (2 - r) - 1, 1), (0, 0, 1))
            st[_idx('R', r, c)] = ((1, (2 - r) - 1, (2 - c) - 1), (1, 0, 0))
            st[_idx('L', r, c)] = ((-1, (2 - r) - 1, c - 1), (-1, 0, 0))
            st[_idx('B', r, c)] = (((2 - c) - 1, (2 - r) - 1, -1), (0, 0, -1))
            st[_idx('D', r, c)] = ((c - 1, -1, (2 - r) - 1), (0, -1, 0))
    return st


def _rot(v, a, d=1):
    x, y, z = v
    if d > 0:
        if a == 'x':
            return (x, z, -y)
        if a == 'y':
            return (z, y, -x)
        return (y, -x, z)
    if a == 'x':
        return (x, -z, y)
    if a == 'y':
        return (-z, y, x)
    return (-y, x, z)


AX = {'U': 'y', 'D': 'y', 'R': 'x', 'L': 'x', 'F': 'z', 'B': 'z'}
LY = {'U': ('y', 1), 'D': ('y', -1), 'R': ('x', 1),
      'L': ('x', -1), 'F': ('z', 1), 'B': ('z', -1)}
# Turn direction per face so every face turns clockwise as viewed from outside
# (Singmaster). A single fixed-handedness _rot makes the +axis face and the
# -axis face on the same axis turn the same geometric way, which inverts three
# of them relative to a real cube. U/L/B sit on the side whose outward normal
# opposes the raw _rot sense, so they take d=-1.
DIR = {'U': -1, 'R': 1, 'F': 1, 'D': 1, 'L': -1, 'B': -1}


def _base():
    st = _sticker()
    lk = {v: k for k, v in st.items()}
    mv = {}
    for f in FACES:
        a = AX[f]
        la, lv = LY[f]
        d = DIR[f]
        p = list(range(54))
        for i, (co, n) in st.items():
            co_d = {'x': co[0], 'y': co[1], 'z': co[2]}
            if co_d[la] == lv:
                p[lk[(_rot(co, a, d), _rot(n, a, d))]] = i
        mv[f] = p
    return mv


def _inv(p):
    q = [0] * 54
    for d, s in enumerate(p):
        q[s] = d
    return q


base = _base()
MOVES = {}
for f in FACES:
    MOVES[f] = base[f]
    MOVES[f + "'"] = _inv(base[f])
    MOVES[f + "2"] = [base[f][base[f][d]] for d in range(54)]

LEGAL = set(MOVES.keys())


def apply_move(state, name):
    p = MOVES[name]
    return ''.join(state[p[d]] for d in range(54))


def apply_seq(state, tokens):
    s = state
    for t in tokens:
        s = apply_move(s, t)
    return s


def build_target(rec):
    """Independently rebuild the target cube state; None if invalid."""
    typ = rec.get("type")
    state = rec.get("state")
    if state is None:
        return None
    if typ == "facelet":
        if not isinstance(state, str) or len(state) != 54:
            return None
        if any(ch not in "URFDLB" for ch in state):
            return None
        if any(state.count(ch) != 9 for ch in "URFDLB"):
            return None
        return state
    if typ == "scramble":
        toks = state.split()
        if any(t not in LEGAL for t in toks):
            return None
        return apply_seq(SOLVED, toks)
    return None


INPUT = "/app/inputs/data"
files = sorted(f for f in os.listdir(INPUT) if f.endswith(".json"))

# Independent dedup: highest priority; tie -> filename sorts last, then later index.
chosen = {}
for fn in files:
    with open(os.path.join(INPUT, fn)) as fh:
        arr = json.load(fh)
    for i, rec in enumerate(arr):
        rid = rec["id"]
        key = (rec.get("priority", 0), fn, i)
        if rid not in chosen or key > chosen[rid][0]:
            chosen[rid] = (key, rec)
resolved = {rid: v[1] for rid, v in chosen.items()}

with open("/app/result.json") as fh:
    result = json.load(fh)

assert isinstance(result, list), "result.json must be a JSON array"

ids = [e["id"] for e in result]
assert ids == sorted(ids), "entries must be sorted by id ascending: %r" % ids
assert ids == sorted(resolved.keys()), "id set mismatch: %r vs %r" % (
    ids, sorted(resolved.keys()))

by_id = {e["id"]: e for e in result}

for rid, rec in resolved.items():
    entry = by_id[rid]
    target = build_target(rec)
    if target is None:
        assert "error" in entry, "id %s should be an error entry" % rid
        assert "moves" not in entry, "invalid id %s must not carry moves" % rid
        continue
    assert "moves" in entry and entry.get("solved") is True, \
        "id %s should be solved" % rid
    moves = entry["moves"]
    assert isinstance(moves, list), "moves must be a list for %s" % rid
    for m in moves:
        assert m in LEGAL, "illegal move %r in %s" % (m, rid)
        assert m[0] in FACES, "move %r is not a real face turn" % m
    assert apply_seq(target, moves) == SOLVED, \
        "moves for %s do not restore the cube" % rid

# already-solved must be an empty sequence
assert by_id["case-01-solved"]["moves"] == [], "solved cube must yield []"

# near-identical twins: each solves its OWN state, not the other's
t3 = build_target(resolved["case-03-twin"])
t4 = build_target(resolved["case-04-twin"])
assert t3 != t4, "twin fixtures must be different cubes"
m3 = by_id["case-03-twin"]["moves"]
assert apply_seq(t3, m3) == SOLVED
assert apply_seq(t4, m3) != SOLVED, "case-03 moves must not solve case-04"

# invalid record -> error entry present
assert "error" in by_id["case-05-invalid"], "invalid record must report error"

# duplicate id resolves to the higher-priority record (from cubes_b: 'F R2')
dup_target = build_target(resolved["case-06-dup"])
expected_dup = apply_seq(SOLVED, "F R2".split())
other_dup = apply_seq(SOLVED, "L2 D".split())
assert dup_target == expected_dup, "dedup must pick the higher-priority record"
mdup = by_id["case-06-dup"]["moves"]
assert apply_seq(expected_dup, mdup) == SOLVED
assert apply_seq(other_dup, mdup) != SOLVED, \
    "resolved dup must not correspond to the lower-priority record"

print("content checks passed")
PY

# determinism: rerun the tool twice and compare byte-for-byte
python3 /app/solution.py --input /app/inputs/data --output /app/_run1.json
python3 /app/solution.py --input /app/inputs/data --output /app/_run2.json
if ! cmp -s /app/_run1.json /app/_run2.json; then
  echo "FAIL: output is not deterministic"
  exit 1
fi
if ! cmp -s /app/_run1.json /app/result.json; then
  echo "FAIL: result.json differs from a fresh run"
  exit 1
fi

echo "ALL TESTS PASSED"
