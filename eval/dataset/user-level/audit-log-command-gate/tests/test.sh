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

OUT=/app/result.json
if [ ! -f "$OUT" ]; then
  echo "FAIL: $OUT does not exist" >&2
  exit 1
fi

python3 - "$OUT" <<'PY'
import json, sys, re, glob, os

out_path = sys.argv[1]
with open(out_path, encoding='utf-8') as fh:
    raw_text = fh.read()
res = json.loads(raw_text)

POLICY = "/app/inputs/policy.json"
LOGS = "/app/inputs/logs"
with open(POLICY, encoding='utf-8') as fh:
    pol = json.load(fh)
allowed = pol['allowed_prefixes']
markers = pol['injection_markers']

ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

# --- independent reimplementation of the verdict policy ---
def strip_literals(cmd):
    buf = []
    k = 0
    L = len(cmd)
    while k < L:
        ch = cmd[k]
        if ch == '<' and k + 1 < L and cmd[k + 1] == '<':
            buf.append(' ' * (L - k)); k = L; break
        if ch == "'":
            e = cmd.find("'", k + 1)
            if e < 0:
                buf.append(' ' * (L - k)); break
            buf.append(' ' * (e - k + 1)); k = e + 1; continue
        if ch == '"':
            e = k + 1
            while e < L:
                if cmd[e] == '\\':
                    e += 2; continue
                if cmd[e] == '"':
                    break
                e += 1
            if e >= L:
                buf.append(' ' * (L - k)); break
            buf.append(' ' * (e - k + 1)); k = e + 1; continue
        buf.append(ch); k += 1
    return ''.join(buf)

def verdict_of(cmd):
    masked = strip_literals(cmd)
    if any(m in masked for m in markers):
        return "command_injection_detected", None
    toks = cmd.split()
    i = 0
    env = []
    while i < len(toks) and ASSIGN.match(toks[i]):
        env.append(toks[i]); i += 1
    rest = ' '.join(toks[i:])
    best = None
    for p in allowed:
        if rest == p or rest.startswith(p + ' '):
            if best is None or len(p) > len(best):
                best = p
    if best is None:
        return "none", None
    pref = (' '.join(env) + ' ' + best) if env else best
    return pref, pref

valid = {}
invalid = []
for path in sorted(glob.glob(os.path.join(LOGS, '*.jsonl'))):
    stem = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding='utf-8') as fh:
        for ln, raw in enumerate(fh, 1):
            line = raw.rstrip('\n')
            if line.strip() == '':
                continue
            try:
                obj = json.loads(line)
            except Exception:
                invalid.append({"source": stem, "line": ln, "reason": "malformed_json"}); continue
            if not isinstance(obj, dict):
                invalid.append({"source": stem, "line": ln, "reason": "malformed_json"}); continue
            if 'command' not in obj:
                invalid.append({"source": stem, "line": ln, "reason": "missing_command"}); continue
            src = obj.get('source', stem)
            rid = obj.get('record_id')
            v, p = verdict_of(obj['command'])
            valid[(src, rid)] = {"record_id": rid, "source": src, "verdict": v, "prefix": p}

exp_records = [valid[k] for k in sorted(valid.keys(), key=lambda k: (str(k[0]), str(k[1])))]
invalid.sort(key=lambda e: (e["source"], e["line"]))
bv = {}
for r in exp_records:
    bv[r["verdict"]] = bv.get(r["verdict"], 0) + 1
exp_summary = {
    "total": len(exp_records),
    "by_verdict": bv,
    "injections": sum(1 for r in exp_records if r["verdict"] == "command_injection_detected"),
    "unresolved": sum(1 for r in exp_records if r["verdict"] == "none"),
    "invalid_records": len(invalid),
}

def fail(msg):
    print("FAIL:", msg); sys.exit(1)

# --- full structural equality ---
if res.get("records") != exp_records:
    fail("records mismatch\nexpected=%s\ngot=%s" % (exp_records, res.get("records")))
if res.get("summary") != exp_summary:
    fail("summary mismatch\nexpected=%s\ngot=%s" % (exp_summary, res.get("summary")))
if res.get("invalid") != invalid:
    fail("invalid mismatch\nexpected=%s\ngot=%s" % (invalid, res.get("invalid")))

# --- explicit spot checks tied to the fixture ---
by_key = {(r["source"], r["record_id"]): r for r in res["records"]}

def check(src, rid, verdict, prefix):
    r = by_key.get((src, rid))
    if r is None:
        fail("missing row (%s,%s)" % (src, rid))
    if r["verdict"] != verdict:
        fail("(%s,%s) verdict=%r expected %r" % (src, rid, r["verdict"], verdict))
    if r["prefix"] != prefix:
        fail("(%s,%s) prefix=%r expected %r" % (src, rid, r["prefix"], prefix))

check("agent-a", "a01", "cat", "cat")
check("agent-a", "a02", "git commit", "git commit")
check("agent-a", "a03", "docker kill", "docker kill")
check("agent-a", "a04", "PYTHONPATH=/tmp python3", "PYTHONPATH=/tmp python3")
check("agent-a", "a05", "npm run lint", "npm run lint")
check("agent-a", "a08", "none", None)                       # empty command
check("agent-a", "a09", "python3", "python3")               # quoted ;| not injection
check("agent-b", "b01", "grep", "grep")                     # 2>/dev/null redirection
check("agent-b", "b02", "command_injection_detected", None) # $(...) substitution
check("agent-b", "b03", "command_injection_detected", None) # pwd; curl
check("agent-b", "b04", "none", None)                       # catalog != cat
check("agent-b", "b05", "git status", "git status")         # longest match
check("agent-b", "b06", "mkdir", "mkdir")                   # dedup keeps last (was ls)
check("agent-b", "a01", "tail", "tail")                     # cross-source distinct row
check("agent-b", "b09", "cd", "cd")                         # claimed_prefix ignored

# invalid entries
if {"source": "agent-a", "line": 6, "reason": "malformed_json"} not in res["invalid"]:
    fail("expected malformed_json at agent-a line 6")
if {"source": "agent-a", "line": 7, "reason": "missing_command"} not in res["invalid"]:
    fail("expected missing_command at agent-a line 7")

# ordering assertion
order = [(r["source"], r["record_id"]) for r in res["records"]]
if order != sorted(order, key=lambda t: (str(t[0]), str(t[1]))):
    fail("records not sorted by (source, record_id)")

# no sensitive strings should leak into the report
for leak in ("secrets", "example.com", "base64", "rm -rf"):
    if leak in raw_text:
        fail("sensitive token leaked into output: %r" % leak)

print("PASS: all assertions succeeded")
PY
