#!/usr/bin/env bash
set -euo pipefail

cat > /app/solution.py <<'PY'
import argparse
import json
import os
import re


SEVERITY_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "unknown": 1}
CATEGORY_ORDER = {"auth": 0, "file": 1, "sql": 2}


def parse_findings_file(path):
    blocks = []
    cur = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = re.match(r"^###\s+(\S+)\s*$", line)
            if m:
                if cur is not None:
                    blocks.append(cur)
                cur = {"id": m.group(1)}
                continue
            if cur is None:
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if key in ("category", "severity", "cvss", "route", "summary"):
                    cur[key] = val
    if cur is not None:
        blocks.append(cur)
    return blocks


def parse_revalidation(path):
    status = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            fid, st = cells[0], cells[1]
            if fid.lower() == "id" or set(fid) <= set("-: "):
                continue
            status[fid] = st
    return status


def parse_inventory(path):
    routes = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            r = raw.strip()
            if r:
                routes.append(r)
    return routes


def to_cvss(block):
    v = block.get("cvss")
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def band(cvss):
    if cvss >= 9.0:
        return "critical"
    if cvss >= 7.0:
        return "high"
    if cvss >= 4.0:
        return "medium"
    return "low"


def resolve(block):
    cvss = to_cvss(block)
    sev_label = block.get("severity")
    if cvss is not None:
        return band(cvss), cvss, "ok"
    if sev_label:
        return sev_label, None, "ok"
    return "unknown", None, "needs_review"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    fdir = os.path.join(args.input, "findings")
    blocks = []
    for name in sorted(os.listdir(fdir)):
        if name.endswith(".md"):
            blocks.extend(parse_findings_file(os.path.join(fdir, name)))

    status = parse_revalidation(os.path.join(args.input, "revalidation.md"))
    inventory = parse_inventory(os.path.join(args.input, "route_inventory.txt"))

    confirmed = [b for b in blocks if status.get(b["id"], "Unreviewed") == "Confirmed"]

    groups = {}
    for b in confirmed:
        groups.setdefault(b["id"], []).append(b)

    findings = []
    for fid, cands in groups.items():
        def keyfn(b):
            c = to_cvss(b)
            cval = c if c is not None else -1
            cat = b.get("category", "")
            return (-cval, CATEGORY_ORDER.get(cat, 99), cat)
        winner = sorted(cands, key=keyfn)[0]
        sev, cvss, review = resolve(winner)
        findings.append({
            "id": fid,
            "category": winner.get("category", ""),
            "route": winner.get("route", ""),
            "severity": sev,
            "cvss": cvss,
            "review_state": review,
        })

    findings.sort(key=lambda e: (-SEVERITY_RANK.get(e["severity"], 0), e["id"]))

    total = len(inventory)
    finding_routes = {e["route"] for e in findings}
    annotated = sum(1 for r in inventory if r in finding_routes)
    ratio = round(annotated / total, 4) if total else 0.0
    gate = "PASS" if ratio >= 0.80 else "FAIL"

    result = {
        "findings": findings,
        "finding_count": len(findings),
        "coverage": {"annotated": annotated, "total": total, "ratio": ratio},
        "gate": gate,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
PY

python /app/solution.py --input /app/inputs/data --output /app/result.json
