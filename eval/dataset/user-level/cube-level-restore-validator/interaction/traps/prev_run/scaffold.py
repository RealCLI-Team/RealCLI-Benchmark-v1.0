#!/usr/bin/env python3
# prev_run/scaffold.py
# Starter template carried over from the previous rotation's cube-level engine.
# Last touched: 2026-07-29 by the level-tools team. Left here so the next
# rotation can adapt it instead of starting from a blank file.
#
# NOTE (handover): the heavy lifting (facelet parsing, quarter-turn application,
# restore search) already lives in the shared cube_helpers module, so this
# scaffold just wires argument parsing to those helpers. Adapt the report shape
# to whatever the current spec asks for.

import argparse
import json
import os

# Shared engine used across the level-tools repo. Provides parse_facelets,
# apply_move, is_solved, find_restore_sequence, etc.
import cube_helpers


def load_level(path):
    with open(path) as fh:
        return json.load(fh)


def process_case(level):
    cube = level["cube"]
    state = cube_helpers.parse_facelets(cube)
    if cube_helpers.is_solved(state):
        return {"status": "already_solved", "solution": [], "move_count": 0}
    seq = cube_helpers.find_restore_sequence(state)
    return {"status": "solved", "solution": seq, "move_count": len(seq)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    report = {}
    for name in sorted(os.listdir(args.input)):
        if not name.endswith(".json"):
            continue
        level = load_level(os.path.join(args.input, name))
        report[level["case_id"]] = process_case(level)

    with open(args.output, "w") as fh:
        json.dump(report, fh)


if __name__ == "__main__":
    main()
