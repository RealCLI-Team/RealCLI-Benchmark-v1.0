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

RESULT=/app/result.json
if [ ! -f "$RESULT" ]; then
  echo "FAIL: $RESULT not found"
  exit 1
fi

cat > /app/_ref.py <<'PY'
import ast
import argparse
import json
import os
import re
import sys
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
        return None, False
    args = split_args(m.group(1))
    if not args:
        return None, False
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


def _legacy_is_injectable(expr):
    if re.search(r'\bf["\']', expr):
        return True
    if "+" in expr:
        return True
    if ".format(" in expr:
        return True
    if re.search(r"%\s*[\(\w]", expr):
        return True
    return False


def _has_nonliteral(node):
    for sub in ast.walk(node):
        if isinstance(
            sub, (ast.Name, ast.Attribute, ast.Call, ast.Subscript, ast.FormattedValue)
        ):
            return True
    return False


def is_injectable(expr, has_params=False):
    if expr is None:
        return False
    try:
        tree = ast.parse(expr.strip(), mode="eval").body
    except (SyntaxError, ValueError):
        return _legacy_is_injectable(expr)
    # Plain string literal is NOT injectable -- this covers Rule 2's explicit
    # case of a literal passed with bound parameters (? / %s placeholders).
    if isinstance(tree, ast.Constant):
        return False
    # f-string: injectable only when a non-literal is actually interpolated.
    if isinstance(tree, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in tree.values)
    # Concatenation: injectable only when a non-literal is mixed in.
    if isinstance(tree, ast.BinOp) and isinstance(tree.op, ast.Add):
        return _has_nonliteral(tree)
    # % formatting: injectable only when a non-literal is mixed in.
    if isinstance(tree, ast.BinOp) and isinstance(tree.op, ast.Mod):
        return _has_nonliteral(tree.left) or _has_nonliteral(tree.right)
    # .format(...): injectable only with a non-literal argument.
    if (
        isinstance(tree, ast.Call)
        and isinstance(tree.func, ast.Attribute)
        and tree.func.attr == "format"
    ):
        return any(_has_nonliteral(a) for a in tree.args) or any(
            _has_nonliteral(kw.value) for kw in tree.keywords
        )
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
            return path, sinkfunc, []
    for (path, handler) in routes:
        h = byname.get(handler)
        if not h or not reads_request(h):
            continue
        c1 = calls_of(h, funcnames)
        if fname in c1:
            return path, h, []
        for mid in c1:
            mf = byname.get(mid)
            if mf and fname in calls_of(mf, funcnames):
                return path, h, [mf]
    return None, None, []


def guard_on_path(handler, mids, sinkfunc, sink_line):
    chain = ([handler] if handler is not None else []) + list(mids)
    for fn in chain:
        for (_, txt) in fn["body"]:
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
    rel = finding["file"]
    line = finding["line"]
    txt = line_text(codebase, rel, line)
    base = {
        "finding_id": finding["finding_id"],
        "verdict": "false_positive",
        "reachable": False,
        "cvss_vector": None,
        "sink_location": None,
        "attack_path": "",
        "false_positive_reason": None,
    }
    if txt is None or not re.search(r"\b(?:db|cursor)\.execute\s*\(", txt):
        base["false_positive_reason"] = "reference mismatch"
        return base
    func = find_container(funcs, rel, line)
    if func is None:
        base["false_positive_reason"] = "reference mismatch"
        return base
    expr, has_params = query_expr(func, line, txt)
    injectable = is_injectable(expr, has_params)
    path, handler, mids = reachability(func, routes, funcs)
    reachable = path is not None
    base["reachable"] = reachable
    if injectable and reachable:
        pr_low = guard_on_path(handler, mids, func, line)
        i_high = first_keyword(expr) in ("INSERT", "UPDATE", "DELETE")
        base["verdict"] = "confirmed"
        base["cvss_vector"] = build_cvss(pr_low, i_high)
        base["sink_location"] = {"file": rel, "line": line}
        base["attack_path"] = "%s => %s:%d" % (path, rel, line)
    else:
        base["false_positive_reason"] = "false positive"
    return base


LEAK = [
    re.compile(r"https?://"),
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"(^|[\s\"'])/(app|home|usr|volume|root|tmp|etc)\b"),
    re.compile(r"\b[0-9a-f]{16,}\b"),
]


def leak_scan(obj):
    if isinstance(obj, str):
        for pat in LEAK:
            if pat.search(obj):
                return obj
    elif isinstance(obj, dict):
        for v in obj.values():
            r = leak_scan(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = leak_scan(v)
            if r:
                return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codebase", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--result", required=True)
    args = ap.parse_args()

    missing = []
    if not Path(args.codebase).is_dir():
        missing.append(args.codebase)
    if not Path(args.findings).is_file():
        missing.append(args.findings)
    if missing:
        print("FAIL: required inputs missing: %s" % ", ".join(missing))
        print("The inputs tree appears to have been wiped or moved during the session.")
        sys.exit(1)

    findings = json.loads(Path(args.findings).read_text())
    funcs = parse_functions(args.codebase)
    routes = parse_routes(args.codebase)

    seen, unique = set(), []
    for f in findings:
        if f["finding_id"] in seen:
            continue
        seen.add(f["finding_id"])
        unique.append(f)
    expected = sorted(
        (verify(args.codebase, f, funcs, routes) for f in unique),
        key=lambda r: r["finding_id"],
    )
    exp_by_id = {r["finding_id"]: r for r in expected}

    got = json.loads(Path(args.result).read_text())
    errors = []

    if got.get("reviewed") != len(unique):
        errors.append("reviewed expected %d got %r" % (len(unique), got.get("reviewed")))

    gf = got.get("findings")
    if not isinstance(gf, list):
        errors.append("findings must be a list")
        print("\n".join(errors))
        sys.exit(1)

    exp_ids = [r["finding_id"] for r in expected]
    got_ids = [r.get("finding_id") for r in gf]
    if got_ids != exp_ids:
        errors.append("finding ordering/dedup expected %r got %r" % (exp_ids, got_ids))

    leaked = leak_scan(got)
    if leaked:
        errors.append("leaked sensitive-looking value: %r" % leaked)

    for gi in gf:
        fid = gi.get("finding_id")
        ei = exp_by_id.get(fid)
        if ei is None:
            errors.append("unexpected finding_id %r" % fid)
            continue
        for key in ("verdict", "reachable", "cvss_vector", "sink_location"):
            if gi.get(key) != ei[key]:
                errors.append("%s.%s expected %r got %r" % (fid, key, ei[key], gi.get(key)))
        exp_reason_null = ei["false_positive_reason"] is None
        got_reason = gi.get("false_positive_reason")
        if exp_reason_null and got_reason is not None:
            errors.append("%s: false_positive_reason must be null when confirmed" % fid)
        if not exp_reason_null and not (isinstance(got_reason, str) and got_reason.strip()):
            errors.append("%s: false_positive_reason must be a non-null string" % fid)
        if ei["verdict"] == "confirmed":
            ap_str = gi.get("attack_path") or ""
            loc = ei["sink_location"]
            needle = "%s:%d" % (loc["file"], loc["line"])
            if needle not in ap_str:
                errors.append("%s: attack_path must contain %r, got %r" % (fid, needle, ap_str))
        else:
            if gi.get("attack_path") != "":
                errors.append("%s: attack_path must be empty for false_positive" % fid)

    if errors:
        print("FAIL")
        print("\n".join(errors))
        sys.exit(1)
    print("PASS: %d findings verified" % len(gf))


if __name__ == "__main__":
    main()
PY

python /app/_ref.py \
  --codebase /app/inputs/codebase \
  --findings /app/inputs/findings.json \
  --result "$RESULT"
