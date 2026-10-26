#!/usr/bin/env bash
set -euo pipefail

cat > /app/solution.py <<'PY'
import argparse
import json
import os
import re
from pathlib import Path

REQ_PATTERNS = ("request.args", "request.form", "request.values", "request.json")
GUARDS = ("require_auth", "require_admin", "require_role")


def file_lines(codebase, rel):
    p = Path(codebase) / rel
    if not p.exists():
        return None
    return p.read_text().splitlines()


def line_text(codebase, rel, line):
    ls = file_lines(codebase, rel)
    if ls is None or line < 1 or line > len(ls):
        return None
    return ls[line - 1]


def parse_functions(codebase):
    funcs = []
    root = Path(codebase)
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        lines = path.read_text().splitlines()
        idxs = [i for i, l in enumerate(lines) if re.match(r"def\s+\w+\s*\(", l)]
        for k, i in enumerate(idxs):
            name = re.match(r"def\s+(\w+)", lines[i]).group(1)
            end = idxs[k + 1] if k + 1 < len(idxs) else len(lines)
            body = [(j + 1, lines[j]) for j in range(i, end)]
            funcs.append({"name": name, "file": rel, "start": i + 1, "body": body})
    return funcs


def parse_routes(codebase):
    lines = file_lines(codebase, "app.py") or []
    routes = []
    for i, l in enumerate(lines):
        m = re.search(r'@app\.route\(\s*"([^"]+)"', l)
        if m:
            for j in range(i + 1, len(lines)):
                dm = re.match(r"def\s+(\w+)", lines[j])
                if dm:
                    routes.append((m.group(1), dm.group(1)))
                    break
    return routes


def reads_request(func):
    return any(p in txt for (_, txt) in func["body"] for p in REQ_PATTERNS)


def calls_of(func, funcnames):
    called = set()
    for (_, txt) in func["body"][1:]:
        for n in funcnames:
            if n != func["name"] and re.search(r"\b" + re.escape(n) + r"\s*\(", txt):
                called.add(n)
    return called


def split_args(argstr):
    parts, depth, cur = [], 0, ""
    for ch in argstr:
        if ch in "([{":
            depth += 1
            cur += ch
        elif ch in ")]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def query_expr(func, sink_line, sink_txt):
    m = re.search(r"\.execute\(\s*(.*)\)\s*$", sink_txt)
    if not m:
        return None
    args = split_args(m.group(1))
    if not args:
        return None
    arg0 = args[0]
    has_params = len(args) >= 2
    if re.match(r"^\w+$", arg0):
        for (ln, txt) in func["body"]:
            if ln >= sink_line:
                break
            am = re.match(r"\s*" + re.escape(arg0) + r"\s*=\s*(.+)$", txt)
            if am:
                return am.group(1), has_params
        return None, has_params
    return arg0, has_params


def is_injectable(expr):
    if expr is None:
        return False
    if re.search(r'\bf["\']', expr):
        return True
    if "+" in expr:
        return True
    if ".format(" in expr:
        return True
    if re.search(r"%\s*[\(\w]", expr):
        return True
    return False


def first_keyword(expr):
    m = re.search(r'["\']\s*(\w+)', expr or "")
    return m.group(1).upper() if m else ""


def reachability(sinkfunc, routes, funcs):
    byname = {f["name"]: f for f in funcs}
    funcnames = set(byname)
    fname = sinkfunc["name"]
    for (path, handler) in routes:
        if handler == fname and reads_request(sinkfunc):
            return path, sinkfunc
    for (path, handler) in routes:
        h = byname.get(handler)
        if not h or not reads_request(h):
            continue
        c1 = calls_of(h, funcnames)
        if fname in c1:
            return path, h
        for mid in c1:
            mf = byname.get(mid)
            if mf and fname in calls_of(mf, funcnames):
                return path, h
    return None, None


def guard_on_path(handler, sinkfunc, sink_line):
    if handler is not None:
        for (_, txt) in handler["body"]:
            if any(g + "(" in txt for g in GUARDS):
                return True
    for (ln, txt) in sinkfunc["body"]:
        if ln >= sink_line:
            break
        if any(g + "(" in txt for g in GUARDS):
            return True
    return False


def build_cvss(pr_low, i_high):
    pr = "L" if pr_low else "N"
    imp = "H" if i_high else "N"
    return "CVSS:3.1/AV:N/AC:L/PR:%s/UI:N/S:U/C:H/I:%s/A:N" % (pr, imp)


def find_container(funcs, rel, line):
    for f in funcs:
        if f["file"] == rel and f["start"] <= line <= f["body"][-1][0]:
            return f
    return None


def verify(codebase, finding, funcs, routes):
    fid = finding["finding_id"]
    rel = finding["file"]
    line = finding["line"]
    txt = line_text(codebase, rel, line)
    base = {
        "finding_id": fid,
        "verdict": "false_positive",
        "reachable": False,
        "cvss_vector": None,
        "sink_location": None,
        "attack_path": "",
        "false_positive_reason": None,
    }
    if txt is None or not re.search(r"(db|cursor)\.execute\s*\(", txt):
        base["false_positive_reason"] = (
            "reference mismatch: %s:%d does not contain a SQL sink" % (rel, line)
        )
        return base
    func = find_container(funcs, rel, line)
    if func is None:
        base["false_positive_reason"] = (
            "reference mismatch: %s:%d is not inside a route-level function" % (rel, line)
        )
        return base
    expr, has_params = query_expr(func, line, txt)
    injectable = is_injectable(expr)
    path, handler = reachability(func, routes, funcs)
    reachable = path is not None
    base["reachable"] = reachable
    if injectable and reachable:
        pr_low = guard_on_path(handler, func, line)
        i_high = first_keyword(expr) in ("INSERT", "UPDATE", "DELETE")
        base["verdict"] = "confirmed"
        base["cvss_vector"] = build_cvss(pr_low, i_high)
        base["sink_location"] = {"file": rel, "line": line}
        base["attack_path"] = "%s => %s:%d" % (path, rel, line)
        base["false_positive_reason"] = None
    else:
        if not injectable:
            base["false_positive_reason"] = (
                "SQL uses parameterized binding; not an injectable sink"
            )
        elif not reachable:
            base["false_positive_reason"] = (
                "concatenated sink is not reachable from any registered route"
            )
        else:
            base["false_positive_reason"] = "no attacker-controlled input reaches the sink"
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codebase", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    findings = json.loads(Path(args.findings).read_text())
    funcs = parse_functions(args.codebase)
    routes = parse_routes(args.codebase)

    seen = set()
    unique = []
    for f in findings:
        if f["finding_id"] in seen:
            continue
        seen.add(f["finding_id"])
        unique.append(f)

    results = [verify(args.codebase, f, funcs, routes) for f in unique]
    results.sort(key=lambda r: r["finding_id"])

    out = {"reviewed": len(unique), "findings": results}
    Path(args.output).write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
PY

python /app/solution.py \
  --codebase /app/inputs/codebase \
  --findings /app/inputs/findings.json \
  --output /app/result.json
