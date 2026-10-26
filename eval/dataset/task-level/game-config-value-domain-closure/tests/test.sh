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
exit 0
__HARBOR_ORIGINAL_SCRIPT_V02__
set -uo pipefail

FAIL=0

python3 - <<'PY'
import json
import os
import re
import subprocess
import sys
import tempfile

# ---------- independent reference implementation ----------

def find_block(text, brace_pos):
    depth = 0
    for i in range(brace_pos, len(text)):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[brace_pos + 1:i]
    return text[brace_pos + 1:]


def parse_codebase(code_dir):
    enums, classes = {}, {}
    for fname in sorted(f for f in os.listdir(code_dir) if f.endswith('.cs')):
        text = open(os.path.join(code_dir, fname), encoding='utf-8').read()
        for m in re.finditer(r'\benum\s+(\w+)\s*\{', text):
            body = find_block(text, m.end() - 1)
            members = []
            for part in body.split(','):
                t = part.strip()
                if not t:
                    continue
                t = t.split('=')[0].split('//')[0].strip()
                if t:
                    members.append(t)
            enums[m.group(1)] = {"members": members, "file": fname}
        for m in re.finditer(r'\bclass\s+(\w+)\s*(?::\s*([\w]+))?\s*\{', text):
            body = find_block(text, m.end() - 1)
            fields = {}
            for fm in re.finditer(r'public\s+([\w<>\[\]]+)\s+(\w+)\s*;[ \t]*(//[^\n]*)?', body):
                rest = fm.group(3) or ""
                rm = re.search(r'//\s*ref:(\w+)', rest)
                if fm.group(2) not in fields:
                    fields[fm.group(2)] = {"type": fm.group(1),
                                           "ref": rm.group(1) if rm else None,
                                           "file": fname}
            classes[m.group(1)] = {"base": m.group(2), "file": fname, "fields": fields}
    return enums, classes


def resolve(classes, cname, key):
    seen, cur = set(), cname
    while cur and cur in classes and cur not in seen:
        seen.add(cur)
        if key in classes[cur]["fields"]:
            return classes[cur]["fields"][key]
        cur = classes[cur]["base"]
    return None


def expected(input_dir):
    gaps = json.load(open(os.path.join(input_dir, 'gaps.json')))["gaps"]
    enums, classes = parse_codebase(os.path.join(input_dir, 'code'))
    dedup = {}
    for g in gaps:
        tr = (g["domain"], g["runtimeClass"], g["key"])
        if tr in dedup:
            continue
        f = resolve(classes, g["runtimeClass"], g["key"])
        if f is None:
            st, allowed, evfile = "stale", None, None
        elif f["type"] in enums:
            st, allowed, evfile = "closed-enum", sorted(enums[f["type"]]["members"]), f["file"]
        elif f["ref"]:
            st, allowed, evfile = "closed-reference", None, f["file"]
        else:
            st, allowed, evfile = "open", None, f["file"]
        dedup[tr] = {"domain": g["domain"], "runtimeClass": g["runtimeClass"],
                     "key": g["key"], "status": st, "allowed": allowed,
                     "evfile": evfile}
    records = sorted(dedup.values(), key=lambda r: (r["domain"], r["runtimeClass"], r["key"]))
    summary = {"total": len(records), "closed-enum": 0, "closed-reference": 0,
               "open": 0, "stale": 0}
    for r in records:
        summary[r["status"]] += 1
    unclosable = sorted("%s/%s/%s" % (r["domain"], r["runtimeClass"], r["key"])
                        for r in records if r["status"] == "open")
    return records, summary, unclosable


def check(actual_path, input_dir, label):
    errs = []
    if not os.path.exists(actual_path):
        return ["[%s] output file missing: %s" % (label, actual_path)]
    try:
        got = json.load(open(actual_path))
    except Exception as e:
        return ["[%s] output not valid JSON: %s" % (label, e)]

    exp_records, exp_summary, exp_unclosable = expected(input_dir)

    if got.get("summary") != exp_summary:
        errs.append("[%s] summary mismatch: got %s expected %s" %
                    (label, got.get("summary"), exp_summary))
    if got.get("unclosable_value_domains") != exp_unclosable:
        errs.append("[%s] unclosable_value_domains mismatch: got %s expected %s" %
                    (label, got.get("unclosable_value_domains"), exp_unclosable))

    grecords = got.get("records")
    if not isinstance(grecords, list):
        return errs + ["[%s] records is not a list" % label]
    if len(grecords) != len(exp_records):
        errs.append("[%s] record count: got %d expected %d" %
                    (label, len(grecords), len(exp_records)))
        return errs

    # ordering must match exactly
    got_order = [(r.get("domain"), r.get("runtimeClass"), r.get("key")) for r in grecords]
    exp_order = [(r["domain"], r["runtimeClass"], r["key"]) for r in exp_records]
    if got_order != exp_order:
        errs.append("[%s] record ordering mismatch: got %s expected %s" %
                    (label, got_order, exp_order))
        return errs

    for g, e in zip(grecords, exp_records):
        tag = "%s/%s/%s" % (e["domain"], e["runtimeClass"], e["key"])
        if g.get("status") != e["status"]:
            errs.append("[%s] %s status: got %r expected %r" %
                        (label, tag, g.get("status"), e["status"]))
        if g.get("allowed") != e["allowed"]:
            errs.append("[%s] %s allowed: got %r expected %r" %
                        (label, tag, g.get("allowed"), e["allowed"]))
        ev = g.get("evidence") or ""
        if e["evfile"] is not None and e["evfile"] not in ev:
            errs.append("[%s] %s evidence missing file %r: %r" %
                        (label, tag, e["evfile"], ev))
        if e["status"] == "closed-reference" and "Table" not in ev and "table" not in ev:
            errs.append("[%s] %s reference evidence should name a table: %r" %
                        (label, tag, ev))
    return errs


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(content)


all_errs = []

# ---------- Case A: the shipped inputs/data (produced by solve.sh) ----------
all_errs += check("/app/result.json", "/app/inputs/data", "A")

# spot-check a couple of Case A facts explicitly
try:
    a = json.load(open("/app/result.json"))
    recs = {(r["domain"], r["runtimeClass"], r["key"]): r for r in a["records"]}
    r = recs.get(("buff", "AddBuffWithSoldierTypeBuff", "SquadType"))
    if not r or r["status"] != "closed-enum" or r["allowed"] != ["Cavalry", "Melee", "Ranged"]:
        all_errs.append("[A] SquadType expected closed-enum ['Cavalry','Melee','Ranged'], got %s" % r)
    r = recs.get(("buff", "AdjustReceiveHealBuff", "AdjustType"))
    if not r or r["status"] != "stale" or r["allowed"] is not None:
        all_errs.append("[A] AdjustType expected stale/null, got %s" % r)
    r = recs.get(("buff", "AddBuffWithSoldierTypeBuff", "SoldierType"))
    if not r or r["status"] != "closed-reference":
        all_errs.append("[A] SoldierType expected closed-reference, got %s" % r)
except Exception as e:
    all_errs.append("[A] could not spot-check result.json: %s" % e)

root = tempfile.mkdtemp(prefix="cases_")

def run_solution(input_dir, out):
    return subprocess.run([sys.executable, "/app/solution.py",
                           "--input", input_dir, "--output", out]).returncode

# ---------- Case B: same field name, different types in two classes ----------
b = os.path.join(root, "B")
write(os.path.join(b, "code", "Elements.cs"),
      "public enum ElementType { Water, Wind }\n")
write(os.path.join(b, "code", "Skills.cs"),
      "public class FireSkill { public ElementType Type; }\n"
      "public class GenericSkill { public int Type; }\n")
write(os.path.join(b, "gaps.json"), json.dumps({"gaps": [
    {"domain": "skill", "category": "enum", "key": "Type", "runtimeClass": "GenericSkill"},
    {"domain": "skill", "category": "int",  "key": "Type", "runtimeClass": "FireSkill"},
]}))
if run_solution(b, os.path.join(b, "out.json")) != 0:
    all_errs.append("[B] solution.py failed to run")
all_errs += check(os.path.join(b, "out.json"), b, "B")

# ---------- Case C: cross-file inheritance, field only on base ----------
c = os.path.join(root, "C")
write(os.path.join(c, "code", "Base.cs"),
      "public enum MoveType { Ground, Air }\n"
      "public class MoveParam { public MoveType MoveType; }\n")
write(os.path.join(c, "code", "Child.cs"),
      "public class FlyingUnit : MoveParam { public int Speed; }\n")
write(os.path.join(c, "gaps.json"), json.dumps({"gaps": [
    {"domain": "unit", "category": "enum", "key": "MoveType", "runtimeClass": "FlyingUnit"},
]}))
if run_solution(c, os.path.join(c, "out.json")) != 0:
    all_errs.append("[C] solution.py failed to run")
all_errs += check(os.path.join(c, "out.json"), c, "C")
try:
    cc = json.load(open(os.path.join(c, "out.json")))
    r = cc["records"][0]
    if r["status"] != "closed-enum" or r["allowed"] != ["Air", "Ground"]:
        all_errs.append("[C] inherited enum expected closed-enum ['Air','Ground'], got %s" % r)
except Exception as e:
    all_errs.append("[C] could not read output: %s" % e)

# ---------- Case D: dedup + shuffled order + stale ----------
d = os.path.join(root, "D")
write(os.path.join(d, "code", "Widget.cs"),
      "public enum ColorType { Red, Blue, Green }\n"
      "public class Widget { public ColorType Color; public int Size; }\n")
write(os.path.join(d, "gaps.json"), json.dumps({"gaps": [
    {"domain": "ui", "category": "open", "key": "Size",    "runtimeClass": "Widget"},
    {"domain": "ui", "category": "enum", "key": "Color",   "runtimeClass": "Widget"},
    {"domain": "ui", "category": "enum", "key": "Color",   "runtimeClass": "Widget"},
    {"domain": "ui", "category": "enum", "key": "Missing", "runtimeClass": "Widget"},
    {"domain": "ui", "category": "open", "key": "Size",    "runtimeClass": "Widget"},
]}))
if run_solution(d, os.path.join(d, "out.json")) != 0:
    all_errs.append("[D] solution.py failed to run")
all_errs += check(os.path.join(d, "out.json"), d, "D")
try:
    dd = json.load(open(os.path.join(d, "out.json")))
    if dd["summary"] != {"total": 3, "closed-enum": 1, "closed-reference": 0, "open": 1, "stale": 1}:
        all_errs.append("[D] dedup summary wrong: %s" % dd["summary"])
    if dd["unclosable_value_domains"] != ["ui/Widget/Size"]:
        all_errs.append("[D] unclosable wrong: %s" % dd["unclosable_value_domains"])
except Exception as e:
    all_errs.append("[D] could not read output: %s" % e)

if all_errs:
    print("FAILED:")
    for e in all_errs:
        print("  -", e)
    sys.exit(1)
print("ALL CHECKS PASSED")
PY
rc=$?
if [ $rc -ne 0 ]; then
    FAIL=1
fi

exit $FAIL
