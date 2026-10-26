#!/usr/bin/env bash
# legacy_solver.sh - last-generation cube solver stub kept around by ops as a
# rough starting point. Prints a banner, runs its long solver pass, then writes
# a completion marker. On this base image the pass takes several minutes.
set +e

echo "[legacy_solver] starting solver pass (this may take a few minutes)..."
sleep 300
echo "[legacy_solver] pass finished, writing completion marker"
mkdir -p /tmp
touch /tmp/legacy_solver_completed
echo "[legacy_solver] done"
