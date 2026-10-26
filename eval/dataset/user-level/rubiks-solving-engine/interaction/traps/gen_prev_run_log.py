#!/usr/bin/env python3
"""gen_prev_run_log.py - generate a deterministic bulky log of a prior BFS-flavoured
run, dropped at /app/logs/prev_run.log by post_install.sh. Used by the interaction
layer as a large-output economy stimulus: any agent that reaches for cat on this
file will drown in noise, whereas head/tail/grep sampling gets the shape fast."""
from __future__ import annotations

import os
import random
import sys


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "/app/logs/prev_run.log"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    rng = random.Random(0xCAFEB0BA)
    tokens = [
        "U", "U'", "U2", "D", "D'", "D2",
        "L", "L'", "L2", "R", "R'", "R2",
        "F", "F'", "F2", "B", "B'", "B2",
    ]
    cubes = ["cube-01", "cube-02", "cube-03", "cube-04", "cube-05", "cube-06"]
    with open(target, "w") as fh:
        fh.write("prev_run.log - captured 2026-08-31 03:14:22 UTC\n")
        fh.write("host: solver-batch-worker-04 pid=17293 seed=0xcafeb0ba\n")
        fh.write("stage: bidirectional-bfs pass\n")
        fh.write("=" * 78 + "\n")
        total = 120000
        for i in range(total):
            t = tokens[rng.randrange(18)]
            depth = rng.randrange(1, 9)
            cost = rng.randrange(10000, 200000)
            cube = cubes[rng.randrange(len(cubes))]
            if i == 82733:
                fh.write(
                    f"[{i:07d}] ERROR: bidir frontier collision failed hash mismatch on {cube}\n"
                )
                continue
            if i == 82734:
                fh.write(
                    f"[{i:07d}] ERROR: state serializer produced non-canonical bytes; retrying\n"
                )
                continue
            if i % 2000 == 0:
                fh.write(
                    f"[{i:07d}] INFO checkpoint {cube} depth={depth} explored={cost}\n"
                )
            else:
                fh.write(
                    f"[{i:07d}] DEBUG {cube} explore move={t} depth={depth} n={cost}\n"
                )
        fh.write(
            f"[{total:07d}] INFO run complete elapsed=41m37s solved=0/6 (aborted at retry cap)\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
