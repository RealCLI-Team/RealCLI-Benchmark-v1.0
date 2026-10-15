#!/usr/bin/env bash
set -euo pipefail
cd /app

python3 - <<'PY'
import json, sys, os

path = "/app/result.json"
assert os.path.exists(path), "result.json not found"
with open(path, encoding="utf-8") as f:
    data = json.load(f)

raw = open(path, encoding="utf-8").read()

assert isinstance(data, dict) and "papers" in data, "missing papers key"
papers = data["papers"]
assert isinstance(papers, list), "papers must be a list"

by_id = {}
KEYS = {"arxiv_id","title","authors","abstract","ppl_wikitext2","venue","github","source","status"}
for p in papers:
    assert set(p.keys()) == KEYS, f"unexpected keys: {set(p.keys())}"
    by_id[p["arxiv_id"]] = p

fail = []
def check(cond, msg):
    if not cond:
        fail.append(msg)

# ---- at least 5 papers across two source types ----
check(len(papers) >= 5, f"expected >=5 papers, got {len(papers)}")
sources = {p["source"] for p in papers}
check(sources == {"detail", "listing"}, f"expected both source types, got {sources}")

# ---- ordering by arxiv_id ascending string compare ----
ids = [p["arxiv_id"] for p in papers]
check(ids == sorted(ids), f"papers not sorted by arxiv_id: {ids}")
check(len(ids) == len(set(ids)), "arxiv_id not unique after grouping")

expected_ids = {"2401.00001","2401.00002","2402.11111","2403.22222","2404.55555","2405.99999"}
check(set(ids) == expected_ids, f"id set mismatch: {set(ids)} vs {expected_ids}")

# ---- detail-vs-listing conflict: detail wins ----
a = by_id.get("2401.00001")
if a:
    check(a["source"] == "detail", "2401.00001 source should be detail")
    check(a["status"] == "CONFIRMED", "2401.00001 status should be CONFIRMED")
    check(a["title"] == "GLoRCQ: Gradient LoRA Quantization",
          f"detail title should win, got {a['title']!r}")
    check(a["ppl_wikitext2"] == 5.68, f"detail ppl should win (5.68), got {a['ppl_wikitext2']!r}")
    check(a["venue"] == "NeurIPS 2024", f"venue mismatch: {a['venue']!r}")
    check(a["github"] == "https://github.com/example/glorcq", f"github mismatch: {a['github']!r}")
    check(a["authors"] == ["Alice Smith","Bob Lee","Carol Ng"], f"authors mismatch: {a['authors']!r}")
    # verbatim abstract snippet, tags stripped, whitespace collapsed
    check("orthogonal Hessian scaling" in a["abstract"], "abstract sentinel missing")
    check(a["abstract"] == "We present GLoRCQ, a quantization method using orthogonal Hessian scaling to preserve accuracy under 4-bit compression.",
          f"abstract not verbatim/collapsed: {a['abstract']!r}")
else:
    fail.append("missing 2401.00001")

# ---- listing-only id ----
e = by_id.get("2404.55555")
if e:
    check(e["source"] == "listing", "2404.55555 source should be listing")
    check(e["status"] == "UNVERIFIED", "2404.55555 status should be UNVERIFIED")
    check(e["ppl_wikitext2"] == 7.01, f"2404.55555 ppl mismatch: {e['ppl_wikitext2']!r}")
    check(e["venue"] == "ICLR 2024", f"2404.55555 venue mismatch: {e['venue']!r}")
    check(e["abstract"] == "We allocate bit-widths adaptively per layer.", f"2404.55555 abstract mismatch: {e['abstract']!r}")
else:
    fail.append("missing 2404.55555")

# ---- record missing PPL and venue -> nulls, still present ----
c = by_id.get("2402.11111")
if c:
    check(c["ppl_wikitext2"] is None, "2402.11111 ppl should be null")
    check(c["venue"] is None, "2402.11111 venue should be null")
    check(c["github"] is None, "2402.11111 github should be null")
    check(c["source"] == "detail", "2402.11111 source should be detail")
    check(c["title"] == "Low-Rank Adaptation Study", f"2402.11111 title mismatch: {c['title']!r}")
else:
    fail.append("missing 2402.11111")

# ---- another detail with venue null but github present ----
b = by_id.get("2401.00002")
if b:
    check(b["venue"] is None, "2401.00002 venue should be null")
    check(b["ppl_wikitext2"] == 6.12, f"2401.00002 ppl mismatch: {b['ppl_wikitext2']!r}")
    check(b["github"] == "https://github.com/example/ir-qlora", f"2401.00002 github mismatch: {b['github']!r}")

# ---- two records same title, different id, both present & distinct ----
d = by_id.get("2403.22222")
f2 = by_id.get("2405.99999")
if d and f2:
    check(d["title"] == "Quantization Meets LoRA", f"2403.22222 title mismatch: {d['title']!r}")
    check(f2["title"] == "Quantization Meets LoRA", f"2405.99999 title mismatch (whitespace not normalized?): {f2['title']!r}")
    check(d["arxiv_id"] != f2["arxiv_id"], "same-title records must stay distinct")
    check(f2["venue"] is None and f2["github"] is None, "2405.99999 venue/github should be null")
else:
    fail.append("missing same-title pair 2403.22222 / 2405.99999")

# ---- no absolute paths / stray URLs outside github ----
def walk(obj, key=None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, k)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, key)
    elif isinstance(obj, str):
        if key != "github":
            check("http://" not in obj and "https://" not in obj, f"stray URL in field {key}: {obj!r}")
            check("/app" not in obj and "/inputs" not in obj, f"absolute path in field {key}: {obj!r}")
walk(data)

if fail:
    print("FAILED:")
    for m in fail:
        print(" -", m)
    sys.exit(1)
print("ALL CHECKS PASSED")
PY
