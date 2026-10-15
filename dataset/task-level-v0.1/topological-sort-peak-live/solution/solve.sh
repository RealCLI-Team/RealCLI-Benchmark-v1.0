#!/usr/bin/env bash
set -euo pipefail

cat > /app/solution.py <<'PY'
#!/usr/bin/env python3
"""Deterministic topological scheduler with a reproducible live-set peak metric."""
import argparse
import glob
import json
import os


def load_fragments(input_dir):
    frags = []
    for path in sorted(glob.glob(os.path.join(input_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as fh:
            frags.append(json.load(fh))
    return frags


def resolve_weight(decls):
    # decls: list of (priority, source_id, weight)
    # choose highest priority; tie -> lexicographically smallest source_id
    best = None
    for prio, sid, weight in decls:
        key = (-int(prio), str(sid))
        if best is None or key < best[0]:
            best = (key, weight)
    return best[1]


def ready_key(node, weights):
    w = weights.get(node)
    if isinstance(w, bool):
        # treat unexpected booleans as null-like; not part of spec
        w = None
    if w is None:
        wk = (1, 0)
    else:
        wk = (0, w)
    return (wk, node)


def build_and_solve(frags):
    errors = []
    node_decls = {}
    sources = set()
    edges = set()
    declared = set()
    referenced = set()

    for fr in frags:
        sid = fr.get("source_id")
        sources.add(sid)
        prio = fr.get("priority", 0)
        for nd in fr.get("nodes", []) or []:
            nid = nd.get("id")
            if nid is None or nid == "":
                errors.append({"reason": "invalid_node_id"})
                continue
            weight = nd.get("weight", None)
            node_decls.setdefault(nid, []).append((prio, sid, weight))
            declared.add(nid)
        for e in fr.get("edges", []) or []:
            frm = e.get("from")
            to = e.get("to")
            if frm is None or frm == "" or to is None or to == "":
                errors.append({"reason": "invalid_edge_endpoint"})
                continue
            edges.add((frm, to))
            referenced.add(frm)
            referenced.add(to)

    nodes = set(declared) | set(referenced)
    weights = {}
    for n in nodes:
        if n in node_decls:
            weights[n] = resolve_weight(node_decls[n])
        else:
            weights[n] = None

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "source_count": len(sources),
    }

    if errors:
        return {
            "status": "error",
            "order": None,
            "cycle_nodes": [],
            "peak_live": 0,
            "stats": stats,
            "errors": errors,
        }

    adj = {n: [] for n in nodes}
    indeg = {n: 0 for n in nodes}
    for (u, v) in edges:
        adj[u].append(v)
        indeg[v] += 1

    indeg_work = dict(indeg)
    ready = [n for n in nodes if indeg_work[n] == 0]
    order = []
    while ready:
        ready.sort(key=lambda n: ready_key(n, weights))
        cur = ready.pop(0)
        order.append(cur)
        for v in adj[cur]:
            indeg_work[v] -= 1
            if indeg_work[v] == 0:
                ready.append(v)

    if len(order) != len(nodes):
        cycle_nodes = sorted(n for n in nodes if indeg_work[n] > 0)
        return {
            "status": "cycle",
            "order": None,
            "cycle_nodes": cycle_nodes,
            "peak_live": 0,
            "stats": stats,
            "errors": [],
        }

    succ_sets = {n: set(adj[n]) for n in nodes}
    peak = 0
    for i in range(len(order)):
        emitted = set(order[: i + 1])
        live = 0
        for n in emitted:
            if any(s not in emitted for s in succ_sets[n]):
                live += 1
        if live > peak:
            peak = live

    return {
        "status": "ok",
        "order": order,
        "cycle_nodes": [],
        "peak_live": peak,
        "stats": stats,
        "errors": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    frags = load_fragments(args.input)
    result = build_and_solve(frags)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")


if __name__ == "__main__":
    main()
PY

echo "solution.py written to /app/solution.py"
