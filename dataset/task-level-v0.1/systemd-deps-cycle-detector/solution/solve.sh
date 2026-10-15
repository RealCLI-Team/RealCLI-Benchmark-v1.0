#!/usr/bin/env bash
set -euo pipefail

# The task image copies inputs/ to /app/inputs (WORKDIR /app).
# Fall back to other common layouts just in case.
if [[ -f /app/inputs/deps.txt ]]; then
    ROOT=/app
elif [[ -f /task/inputs/deps.txt ]]; then
    ROOT=/task
elif [[ -f ./inputs/deps.txt ]]; then
    ROOT="$(pwd)"
else
    echo "ERROR: cannot locate inputs/deps.txt" >&2
    exit 1
fi

cd "$ROOT"
mkdir -p outputs

TASK_ROOT="$ROOT" python3 - <<'PY'
import json, re, os
from pathlib import Path

root = Path(os.environ["TASK_ROOT"])
deps_path = root / "inputs" / "deps.txt"

edges = []  # (dep, unit)
units = set()
adj = {}

for raw in deps_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line:
        continue
    if ":" not in line:
        continue
    unit, rest = line.split(":", 1)
    unit = unit.strip()
    if not unit:
        continue
    units.add(unit)
    adj.setdefault(unit, set())
    rest = rest.strip()

    deps = set()
    for m in re.finditer(r"(After|Requires)=([^\s]+)", rest):
        vals = m.group(2)
        for v in vals.split(","):
            v = v.strip()
            if v:
                deps.add(v)
    for d in deps:
        units.add(d)
        adj.setdefault(d, set())
        adj[unit].add(d)  # unit depends on d; edge dep -> unit
        edges.append((d, unit))

# Build forward graph: dep -> unit
fwd = {u: set() for u in units}
for d, u in edges:
    fwd[d].add(u)

# Write deps.dot
dot_lines = ["digraph deps {"]
for u in sorted(units):
    dot_lines.append(f'  "{u}";')
for d, u in sorted(set(edges)):
    dot_lines.append(f'  "{d}" -> "{u}";')
dot_lines.append("}")
(root / "outputs" / "deps.dot").write_text("\n".join(dot_lines) + "\n", encoding="utf-8")

# Find all simple cycles: Tarjan SCC, then Johnson-style circuit enumeration
# inside each nontrivial SCC.

def tarjan_scc(graph):
    import sys
    sys.setrecursionlimit(100000)
    index = 0
    stack = []
    on_stack = set()
    indices = {}
    lowlink = {}
    sccs = []

    def strongconnect(v, index):
        indices[v] = index
        lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in indices:
                index = strongconnect(w, index)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)
        return index

    for v in list(graph.keys()):
        if v not in indices:
            index = strongconnect(v, index)
    return sccs

sccs = tarjan_scc(fwd)

cycles = []

def find_cycles_in_scc(nodes, graph):
    node_set = set(nodes)
    sub = {n: sorted([m for m in graph[n] if m in node_set]) for n in nodes}
    found = []
    start_order = sorted(nodes)
    blocked = set()
    B = {n: set() for n in nodes}
    stack = []

    def unblock(u):
        blocked.discard(u)
        while B[u]:
            w = B[u].pop()
            if w in blocked:
                unblock(w)

    def circuit(v, s, comp_nodes):
        f = False
        stack.append(v)
        blocked.add(v)
        for w in sub[v]:
            if w not in comp_nodes:
                continue
            if w == s:
                found.append(list(stack))
                f = True
            elif w not in blocked:
                if circuit(w, s, comp_nodes):
                    f = True
        if f:
            unblock(v)
        else:
            for w in sub[v]:
                if w in comp_nodes and v not in B[w]:
                    B[w].add(v)
        stack.pop()
        return f

    remaining = set(nodes)
    for s in start_order:
        if s not in remaining:
            continue
        comp = remaining.copy()
        blocked = set()
        B = {n: set() for n in comp}
        stack = []
        circuit(s, s, comp)
        remaining.discard(s)
    return found

for comp in sccs:
    if len(comp) == 1:
        n = comp[0]
        if n in fwd[n]:
            cycles.append([n])
        continue
    found = find_cycles_in_scc(comp, fwd)
    for c in found:
        cycles.append(c)

# Normalize: rotate each cycle to start at its lexicographically smallest member
normed = set()
result = []
for c in cycles:
    i = min(range(len(c)), key=lambda k: c[k])
    rot = c[i:] + c[:i]
    key = tuple(rot)
    if key not in normed:
        normed.add(key)
        result.append(list(rot))

result.sort()

(root / "outputs" / "cycles.json").write_text(
    json.dumps({"cycles": result}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

echo "done."
