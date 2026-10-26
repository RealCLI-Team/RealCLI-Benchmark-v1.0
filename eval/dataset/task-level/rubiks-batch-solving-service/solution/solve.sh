#!/usr/bin/env bash
set -euo pipefail

# Write the batch cube-solving CLI, then run it on the shipped input package.
cat > /app/solution.py << 'PY'
import argparse, json, os
from collections import deque, Counter, OrderedDict

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
    moves = {}
    for m, (axis, val) in spec.items():
        perm = list(range(54))
        k = ai[axis]
        for i, (cc, nn) in coords.items():
            if cc[k] == val:
                j = pos2idx[(rot(axis, cc), rot(axis, nn))]
                perm[j] = i
        moves[m] = perm
    return moves

MOVES = build_moves()
TOKENS = [b + s for b in "URFDLB" for s in ["", "'", "2"]]

def apply_move(state, token):
    perm = MOVES[token[0]]
    t = 1 if len(token) == 1 else (3 if token[1] == "'" else 2)
    for _ in range(t):
        state = ''.join(state[perm[i]] for i in range(54))
    return state

def inv(tok):
    if len(tok) == 1: return tok + "'"
    if tok[1] == "'": return tok[0]
    return tok

def solve(start, solved):
    if start == solved:
        return []
    fwd = {start: (None, None)}
    bwd = {solved: (None, None)}
    ff = [start]; bf = [solved]
    def rebuild(meet):
        a = []; s = meet
        while fwd[s][0] is not None:
            p, t = fwd[s]; a.append(t); s = p
        a.reverse()
        b = []; s = meet
        while bwd[s][0] is not None:
            p, t = bwd[s]; b.append(inv(t)); s = p
        return a + b
    for _ in range(9):
        if len(ff) <= len(bf):
            nf = []
            for st in ff:
                for tok in TOKENS:
                    ns = apply_move(st, tok)
                    if ns not in fwd:
                        fwd[ns] = (st, tok); nf.append(ns)
                        if ns in bwd: return rebuild(ns)
            ff = nf
        else:
            nf = []
            for st in bf:
                for tok in TOKENS:
                    ns = apply_move(st, tok)
                    if ns not in bwd:
                        bwd[ns] = (st, tok); nf.append(ns)
                        if ns in fwd: return rebuild(ns)
            bf = nf
    return None

def is_valid(state, solved):
    if not isinstance(state, str) or len(state) != 54:
        return False
    cnt = Counter(state)
    if any(k not in "URFDLB" for k in cnt):
        return False
    return all(cnt.get(c, 0) == 9 for c in "URFDLB")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    notation_path = os.path.join(args.input, "notation.json")
    solved = "UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB"
    if os.path.exists(notation_path):
        with open(notation_path) as f:
            solved = json.load(f).get("solved_state", solved)

    order = []
    states = {}
    dup = set()
    with open(os.path.join(args.input, "cubes.jsonl")) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rid = rec["id"]; st = rec.get("state")
            if rid in states:
                dup.add(rid)
            else:
                order.append(rid)
            states[rid] = st

    results = []
    for rid in sorted(order):
        if rid in dup:
            results.append({"id": rid, "status": "invalid", "moves": None, "move_count": 0})
            continue
        st = states[rid]
        if not is_valid(st, solved):
            results.append({"id": rid, "status": "invalid", "moves": None, "move_count": 0})
            continue
        if st == solved:
            results.append({"id": rid, "status": "already_solved", "moves": [], "move_count": 0})
            continue
        sol = solve(st, solved)
        if sol is None:
            results.append({"id": rid, "status": "invalid", "moves": None, "move_count": 0})
        else:
            results.append({"id": rid, "status": "solved", "moves": sol, "move_count": len(sol)})

    with open(args.output, "w") as f:
        json.dump({"results": results}, f, indent=2)

if __name__ == "__main__":
    main()
PY

cd /app
python3 /app/solution.py --input /app/inputs/data --output /app/result.json
