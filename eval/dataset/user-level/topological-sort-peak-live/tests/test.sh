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
# CliBench: collect interaction evidence after the original verifier has
# already decided reward. Never allowed to change the verdict.
if [ -f /tests/collect_evidence.sh ]; then
  bash /tests/collect_evidence.sh || true
fi

exit 0
__HARBOR_ORIGINAL_SCRIPT_V02__
set -euo pipefail

cd /app

python3 - <<'PY'
import json
import os
import random
import subprocess
import sys
import tempfile

SOL = "/app/solution.py"
INP = "/app/inputs"

if not os.path.exists(SOL):
    print("FAIL: /app/solution.py not found", file=sys.stderr)
    sys.exit(1)

failures = []


def run(input_dir):
    out = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
    subprocess.run(
        ["python3", SOL, "--input", input_dir, "--output", out],
        check=True,
    )
    with open(out, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check(name, cond, detail=""):
    if cond:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} {detail}")
        failures.append(name)


# ---- Case A ----
r = run(os.path.join(INP, "caseA"))
check("A.status", r["status"] == "ok", r.get("status"))
check("A.order", r["order"] == ["B", "A", "C", "D"], r.get("order"))
check("A.peak_live", r["peak_live"] == 2, r.get("peak_live"))
check("A.stats", r["stats"] == {"node_count": 4, "edge_count": 3, "source_count": 1}, r.get("stats"))

# ---- Case B ----
r = run(os.path.join(INP, "caseB"))
check("B.status", r["status"] == "ok", r.get("status"))
check("B.order", r["order"] == ["X", "Y", "Z"], r.get("order"))
check("B.node_count", r["stats"]["node_count"] == 3, r["stats"])
check("B.source_count", r["stats"]["source_count"] == 2, r["stats"])
check("B.edge_count", r["stats"]["edge_count"] == 2, r["stats"])
check("B.peak_live", r["peak_live"] == 2, r.get("peak_live"))

# ---- Case C ----
r = run(os.path.join(INP, "caseC"))
check("C.status", r["status"] == "ok", r.get("status"))
check("C.order", r["order"] == ["P", "Q", "R"], r.get("order"))
check("C.edge_count_dedup", r["stats"]["edge_count"] == 2, r["stats"])
check("C.peak_live", r["peak_live"] == 2, r.get("peak_live"))

# ---- Case D cycle ----
r = run(os.path.join(INP, "caseD_cycle"))
check("Dcycle.status", r["status"] == "cycle", r.get("status"))
check("Dcycle.order_null", r["order"] is None, r.get("order"))
check("Dcycle.cycle_nodes", r["cycle_nodes"] == ["A", "B", "C"], r.get("cycle_nodes"))
check("Dcycle.peak", r["peak_live"] == 0, r.get("peak_live"))

# ---- Case D self-loop ----
r = run(os.path.join(INP, "caseD_selfloop"))
check("Dself.status", r["status"] == "cycle", r.get("status"))
check("Dself.cycle_nodes", r["cycle_nodes"] == ["W"], r.get("cycle_nodes"))
check("Dself.order_null", r["order"] is None, r.get("order"))

# ---- Case E error ----
r = run(os.path.join(INP, "caseE"))
check("E.status", r["status"] == "error", r.get("status"))
reasons = sorted(e["reason"] for e in r["errors"])
check("E.reasons", reasons == ["invalid_edge_endpoint", "invalid_node_id"], reasons)
check("E.order_null", r["order"] is None, r.get("order"))
check("E.peak", r["peak_live"] == 0, r.get("peak_live"))

# ---- Case F null vs numeric ----
r = run(os.path.join(INP, "caseF"))
check("F.status", r["status"] == "ok", r.get("status"))
check("F.order", r["order"] == ["H", "G", "K"], r.get("order"))
check("F.peak", r["peak_live"] == 2, r.get("peak_live"))


# ---- Generated random DAG with independent reference ----
def reference(frags):
    errors = []
    decls = {}
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
            decls.setdefault(nid, []).append((prio, sid, nd.get("weight", None)))
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
    nodes = declared | referenced
    weights = {}
    for n in nodes:
        if n in decls:
            best = None
            for prio, sid, w in decls[n]:
                key = (-int(prio), str(sid))
                if best is None or key < best[0]:
                    best = (key, w)
            weights[n] = best[1]
        else:
            weights[n] = None
    stats = {"node_count": len(nodes), "edge_count": len(edges), "source_count": len(sources)}
    if errors:
        return {"status": "error", "order": None, "cycle_nodes": [], "peak_live": 0, "stats": stats, "errors": errors}
    adj = {n: [] for n in nodes}
    indeg = {n: 0 for n in nodes}
    for (u, v) in edges:
        adj[u].append(v)
        indeg[v] += 1

    def keyf(n):
        w = weights[n]
        wk = (1, 0) if w is None else (0, w)
        return (wk, n)

    ready = [n for n in nodes if indeg[n] == 0]
    order = []
    while ready:
        ready.sort(key=keyf)
        cur = ready.pop(0)
        order.append(cur)
        for v in adj[cur]:
            indeg[v] -= 1
            if indeg[v] == 0:
                ready.append(v)
    if len(order) != len(nodes):
        return {"status": "cycle", "order": None,
                "cycle_nodes": sorted(n for n in nodes if indeg[n] > 0),
                "peak_live": 0, "stats": stats, "errors": []}
    succ = {n: set(adj[n]) for n in nodes}
    peak = 0
    for i in range(len(order)):
        emitted = set(order[: i + 1])
        live = sum(1 for n in emitted if any(s not in emitted for s in succ[n]))
        peak = max(peak, live)
    return {"status": "ok", "order": order, "cycle_nodes": [], "peak_live": peak, "stats": stats, "errors": []}


rng = random.Random(20260812)
gen_ok = True
for trial in range(25):
    n = rng.randint(4, 9)
    ids = [f"n{idx:02d}" for idx in range(n)]
    rng.shuffle(ids)
    weights = {}
    for nid in ids:
        weights[nid] = None if rng.random() < 0.3 else rng.randint(0, 6)
    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.35:
                edges.append({"from": ids[i], "to": ids[j]})
    # split nodes into two fragments
    node_objs = [{"id": nid, "weight": weights[nid]} for nid in ids]
    rng.shuffle(node_objs)
    mid = len(node_objs) // 2
    frags = [
        {"source_id": "s1", "priority": 3, "nodes": node_objs[:mid], "edges": edges},
        {"source_id": "s2", "priority": 1, "nodes": node_objs[mid:], "edges": []},
    ]
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.json"), "w") as fh:
        json.dump(frags[0], fh)
    with open(os.path.join(d, "b.json"), "w") as fh:
        json.dump(frags[1], fh)
    got = run(d)
    want = reference(frags)
    if got != want:
        gen_ok = False
        print(f"FAIL: generated trial {trial}\n  got={got}\n  want={want}")
        break

check("generated.random_dag", gen_ok)

if failures:
    print(f"\n{len(failures)} assertion(s) failed")
    sys.exit(1)
print("\nALL TESTS PASSED")
PY
