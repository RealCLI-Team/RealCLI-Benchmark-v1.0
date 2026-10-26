#!/usr/bin/env bash
set -euo pipefail

cat > /app/solution.py <<'PY'
import argparse
import json
import os
import re
import sys


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
    enums = {}    # name -> {"members": [...], "file": fname}
    classes = {}  # name -> {"base": str|None, "file": fname, "fields": {name: {...}}}

    files = sorted(f for f in os.listdir(code_dir) if f.endswith('.cs'))
    for fname in files:
        with open(os.path.join(code_dir, fname), encoding='utf-8') as fh:
            text = fh.read()

        # enums
        for m in re.finditer(r'\benum\s+(\w+)\s*\{', text):
            name = m.group(1)
            body = find_block(text, m.end() - 1)
            members = []
            for part in body.split(','):
                token = part.strip()
                if not token:
                    continue
                token = token.split('=')[0].strip()
                token = token.split('//')[0].strip()
                if token:
                    members.append(token)
            enums[name] = {"members": members, "file": fname}

        # classes
        for m in re.finditer(r'\bclass\s+(\w+)\s*(?::\s*([\w]+))?\s*\{', text):
            cname = m.group(1)
            base = m.group(2)
            body = find_block(text, m.end() - 1)
            fields = {}
            for fm in re.finditer(
                    r'public\s+([\w<>\[\]]+)\s+(\w+)\s*;[ \t]*(//[^\n]*)?', body):
                ftype, fieldname, rest = fm.group(1), fm.group(2), fm.group(3) or ""
                ref = None
                rm = re.search(r'//\s*ref:(\w+)', rest)
                if rm:
                    ref = rm.group(1)
                if fieldname not in fields:
                    fields[fieldname] = {"type": ftype, "ref": ref, "file": fname}
            classes[cname] = {"base": base, "file": fname, "fields": fields}
    return enums, classes


def resolve_field(classes, class_name, key):
    seen = set()
    cur = class_name
    while cur and cur in classes and cur not in seen:
        seen.add(cur)
        cls = classes[cur]
        if key in cls["fields"]:
            return cls["fields"][key]
        cur = cls["base"]
    return None


def classify(gap, enums, classes):
    domain = gap["domain"]
    rc = gap["runtimeClass"]
    key = gap["key"]
    field = resolve_field(classes, rc, key)
    if field is None:
        return {
            "domain": domain, "runtimeClass": rc, "key": key,
            "status": "stale", "allowed": None,
            "evidence": "field '%s' not found on %s or its base classes" % (key, rc),
        }
    fname = field["file"]
    ftype = field["type"]
    if ftype in enums:
        allowed = sorted(enums[ftype]["members"])
        return {
            "domain": domain, "runtimeClass": rc, "key": key,
            "status": "closed-enum", "allowed": allowed,
            "evidence": "%s: field '%s' has enum type %s (defined in %s)" % (
                fname, key, ftype, enums[ftype]["file"]),
        }
    if field["ref"]:
        return {
            "domain": domain, "runtimeClass": rc, "key": key,
            "status": "closed-reference", "allowed": None,
            "evidence": "%s: field '%s' references table %s" % (fname, key, field["ref"]),
        }
    return {
        "domain": domain, "runtimeClass": rc, "key": key,
        "status": "open", "allowed": None,
        "evidence": "%s: field '%s' is an open value domain (type %s)" % (fname, key, ftype),
    }


def analyze(input_dir):
    with open(os.path.join(input_dir, 'gaps.json'), encoding='utf-8') as fh:
        gaps = json.load(fh)["gaps"]
    enums, classes = parse_codebase(os.path.join(input_dir, 'code'))

    dedup = {}
    for gap in gaps:
        triple = (gap["domain"], gap["runtimeClass"], gap["key"])
        if triple not in dedup:
            dedup[triple] = classify(gap, enums, classes)

    records = sorted(
        dedup.values(),
        key=lambda r: (r["domain"], r["runtimeClass"], r["key"]))

    summary = {"total": len(records), "closed-enum": 0,
               "closed-reference": 0, "open": 0, "stale": 0}
    for r in records:
        summary[r["status"]] += 1

    unclosable = sorted(
        "%s/%s/%s" % (r["domain"], r["runtimeClass"], r["key"])
        for r in records if r["status"] == "open")

    return {"records": records, "summary": summary,
            "unclosable_value_domains": unclosable}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/app/inputs/data")
    ap.add_argument("--output", default="/app/result.json")
    args = ap.parse_args()
    report = analyze(args.input)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


if __name__ == "__main__":
    main()
PY

python /app/solution.py --input /app/inputs/data --output /app/result.json
echo "wrote /app/result.json"
