#!/usr/bin/env python3
"""Static allow-list gate for code-agent bash audit logs."""
import argparse
import glob
import json
import os
import re

ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def mask_literals(cmd):
    """Replace quoted / heredoc regions with spaces so injection markers inside
    them are not detected."""
    res = []
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == '<' and i + 1 < n and cmd[i + 1] == '<':
            # heredoc / herestring: treat the rest of the (single-line) command as literal
            res.append(' ' * (n - i))
            break
        if c == "'":
            j = cmd.find("'", i + 1)
            if j == -1:
                res.append(' ' * (n - i))
                break
            res.append(' ' * (j - i + 1))
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if cmd[j] == '\\':
                    j += 2
                    continue
                if cmd[j] == '"':
                    break
                j += 1
            if j >= n:
                res.append(' ' * (n - i))
                break
            res.append(' ' * (j - i + 1))
            i = j + 1
            continue
        res.append(c)
        i += 1
    return ''.join(res)


def is_injection(cmd, markers):
    masked = mask_literals(cmd)
    for m in markers:
        if m in masked:
            return True
    return False


def classify(cmd, allowed, markers):
    if is_injection(cmd, markers):
        return "command_injection_detected", None
    tokens = cmd.split()
    env = []
    idx = 0
    while idx < len(tokens) and ASSIGN_RE.match(tokens[idx]):
        env.append(tokens[idx])
        idx += 1
    rest = ' '.join(tokens[idx:])
    best = None
    for p in allowed:
        if rest == p or rest.startswith(p + ' '):
            if best is None or len(p) > len(best):
                best = p
    if best is None:
        return "none", None
    prefix = (' '.join(env) + ' ' + best) if env else best
    return prefix, prefix


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--policy', required=True)
    ap.add_argument('--logs', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    with open(args.policy, encoding='utf-8') as fh:
        policy = json.load(fh)
    allowed = list(policy['allowed_prefixes'])
    markers = list(policy['injection_markers'])

    valid = {}
    invalid = []

    for path in sorted(glob.glob(os.path.join(args.logs, '*.jsonl'))):
        stem = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding='utf-8') as fh:
            for lineno, raw in enumerate(fh, 1):
                line = raw.rstrip('\n')
                if line.strip() == '':
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    invalid.append({"source": stem, "line": lineno, "reason": "malformed_json"})
                    continue
                if not isinstance(obj, dict):
                    invalid.append({"source": stem, "line": lineno, "reason": "malformed_json"})
                    continue
                if 'command' not in obj:
                    invalid.append({"source": stem, "line": lineno, "reason": "missing_command"})
                    continue
                rid = obj.get('record_id')
                source = obj.get('source', stem)
                verdict, prefix = classify(obj['command'], allowed, markers)
                valid[(source, rid)] = {
                    "record_id": rid,
                    "source": source,
                    "verdict": verdict,
                    "prefix": prefix,
                }

    records = [valid[k] for k in sorted(valid.keys(), key=lambda k: (str(k[0]), str(k[1])))]
    invalid.sort(key=lambda e: (e["source"], e["line"]))

    by_verdict = {}
    for r in records:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1

    result = {
        "records": records,
        "summary": {
            "total": len(records),
            "by_verdict": by_verdict,
            "injections": sum(1 for r in records if r["verdict"] == "command_injection_detected"),
            "unresolved": sum(1 for r in records if r["verdict"] == "none"),
            "invalid_records": len(invalid),
        },
        "invalid": invalid,
    }

    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, indent=2)
        fh.write('\n')


if __name__ == '__main__':
    main()
