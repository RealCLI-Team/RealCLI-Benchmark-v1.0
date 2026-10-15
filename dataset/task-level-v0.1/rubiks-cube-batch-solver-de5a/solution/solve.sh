#!/usr/bin/env bash
set -euo pipefail
cd /app

cat > /app/solution.py <<'PY'
import argparse, json, os, sys
from collections import deque

FACES = ['U', 'R', 'F', 'D', 'L', 'B']
BASE = {'U': 0, 'R': 9, 'F': 18, 'D': 27, 'L': 36, 'B': 45}
SOLVED = ''.join(ch * 9 for ch in "URFDLB")


def _idx(f, r, c):
    return BASE[f] + r * 3 + c


def _build_sticker():
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


def _rot(v, axis, d=1):
    x, y, z = v
    if d > 0:
        if axis == 'x':
            return (x, z, -y)
        if axis == 'y':
            return (z, y, -x)
        return (y, -x, z)
    if axis == 'x':
        return (x, -z, y)
    if axis == 'y':
        return (-z, y, x)
    return (-y, x, z)


_FACE_AXIS = {'U': 'y', 'D': 'y', 'R': 'x', 'L': 'x', 'F': 'z', 'B': 'z'}
_FACE_LAYER = {'U': ('y', 1), 'D': ('y', -1), 'R': ('x', 1),
               'L': ('x', -1), 'F': ('z', 1), 'B': ('z', -1)}
# Turn direction per face so every face turns clockwise as viewed from outside
# (Singmaster). U/L/B take d=-1; see the matching note in tests/_orig_test.sh.
_FACE_DIR = {'U': -1, 'R': 1, 'F': 1, 'D': 1, 'L': -1, 'B': -1}


def _base_moves():
    st = _build_sticker()
    lookup = {v: k for k, v in st.items()}
    moves = {}
    for f in FACES:
        axis = _FACE_AXIS[f]
        la, lv = _FACE_LAYER[f]
        d = _FACE_DIR[f]
        perm = list(range(54))
        for i, (co, n) in st.items():
            coord = {'x': co[0], 'y': co[1], 'z': co[2]}
            if coord[la] == lv:
                perm[lookup[(_rot(co, axis, d), _rot(n, axis, d))]] = i
        moves[f] = perm
    return moves


def _inv(p):
    q = [0] * 54
    for d, s in enumerate(p):
        q[s] = d
    return q


def _twice(p):
    return [p[p[d]] for d in range(54)]


def build_move_table():
    base = _base_moves()
    table = {}
    for f in FACES:
        table[f] = base[f]
        table[f + "'"] = _inv(base[f])
        table[f + "2"] = _twice(base[f])
    return table


MOVES = build_move_table()
MOVE_NAMES = [f + s for f in FACES for s in ('', "'", '2')]


def apply_move(state, name):
    p = MOVES[name]
    return ''.join(state[p[d]] for d in range(54))


def apply_sequence(state, tokens):
    s = state
    for t in tokens:
        s = apply_move(s, t)
    return s


def inverse_name(name):
    if name.endswith("2"):
        return name
    if name.endswith("'"):
        return name[0]
    return name + "'"


def solve(state, max_depth=12):
    if state == SOLVED:
        return []
    fwd = {state: []}
    bwd = {SOLVED: []}
    ff = [state]
    bf = [SOLVED]
    depth = 0
    while depth < max_depth:
        # expand smaller frontier
        if len(ff) <= len(bf):
            nxt = []
            for s in ff:
                last = fwd[s][-1][0] if fwd[s] else None
                for m in MOVE_NAMES:
                    if m[0] == last:
                        continue
                    ns = apply_move(s, m)
                    if ns in fwd:
                        continue
                    fwd[ns] = fwd[s] + [m]
                    if ns in bwd:
                        back = bwd[ns]
                        return fwd[ns] + [inverse_name(b) for b in reversed(back)]
                    nxt.append(ns)
            ff = nxt
        else:
            nxt = []
            for s in bf:
                last = bwd[s][-1][0] if bwd[s] else None
                for m in MOVE_NAMES:
                    if m[0] == last:
                        continue
                    ns = apply_move(s, m)
                    if ns in bwd:
                        continue
                    bwd[ns] = bwd[s] + [m]
                    if ns in fwd:
                        back = bwd[ns]
                        return fwd[ns] + [inverse_name(b) for b in reversed(back)]
                    nxt.append(ns)
            bf = nxt
        depth += 1
    return None


def parse_scramble(text):
    tokens = text.split()
    for t in tokens:
        if t not in MOVES:
            return None, "illegal move token: %s" % t
    return tokens, None


def validate_facelet(s):
    if not isinstance(s, str) or len(s) != 54:
        return "facelet must be a 54-character string"
    allowed = set("URFDLB")
    for ch in s:
        if ch not in allowed:
            return "facelet contains illegal character: %s" % ch
    for ch in "URFDLB":
        if s.count(ch) != 9:
            return "facelet has wrong sticker count for %s" % ch
    return None


def target_state(rec):
    typ = rec.get("type")
    state = rec.get("state")
    if state is None:
        return None, "missing state"
    if typ == "facelet":
        err = validate_facelet(state)
        if err:
            return None, err
        return state, None
    if typ == "scramble":
        tokens, err = parse_scramble(state)
        if err:
            return None, err
        return apply_sequence(SOLVED, tokens), None
    return None, "unknown type: %s" % typ


def load_records(input_dir):
    files = sorted(f for f in os.listdir(input_dir) if f.endswith(".json"))
    chosen = {}
    for fname in files:
        with open(os.path.join(input_dir, fname)) as fh:
            arr = json.load(fh)
        for i, rec in enumerate(arr):
            rid = rec["id"]
            prio = rec.get("priority", 0)
            key = (prio, fname, i)
            if rid not in chosen or key > chosen[rid][0]:
                chosen[rid] = (key, rec)
    return {rid: v[1] for rid, v in chosen.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    records = load_records(args.input)
    out = []
    for rid in sorted(records.keys()):
        rec = records[rid]
        state, err = target_state(rec)
        if err is not None:
            out.append({"id": rid, "error": err})
            continue
        moves = solve(state)
        if moves is None:
            out.append({"id": rid, "error": "no solution within depth limit"})
            continue
        out.append({"id": rid, "moves": moves, "solved": True})

    with open(args.output, "w") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    main()
PY

python3 /app/solution.py --input /app/inputs/data --output /app/result.json
