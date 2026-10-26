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
INPUT=/app/inputs/data

fail() { echo "FAIL: $1" >&2; exit 1; }

[ -f "$RESULT" ] || fail "result.json not found"

python3 - "$RESULT" "$INPUT" <<'PY'
import json, os, re, sys

result_path, input_dir = sys.argv[1], sys.argv[2]
with open(result_path, encoding="utf-8") as f:
    raw = f.read()
r = json.loads(raw)

def check(cond, msg):
    if not cond:
        print("FAIL: " + msg, file=sys.stderr)
        sys.exit(1)

# top-level keys
check(set(r.keys()) == {"findings", "finding_count", "coverage", "gate"},
      "unexpected top-level keys: %r" % sorted(r.keys()))

findings = r["findings"]
by_id = {e["id"]: e for e in findings}

# each element schema
for e in findings:
    check(set(e.keys()) == {"id", "category", "route", "severity", "cvss", "review_state"},
          "bad element keys for %r" % e.get("id"))

# count and ordering
ids = [e["id"] for e in findings]
check(r["finding_count"] == len(findings), "finding_count mismatch")
check(r["finding_count"] == 6, "expected 6 findings, got %d" % r["finding_count"])
check(ids == ["F-101", "F-130", "F-150", "F-160", "F-200", "F-110"],
      "unexpected findings order: %r" % ids)

# excluded ids (FalsePositive / Downgraded / Unreviewed)
for x in ("F-120", "F-140", "F-170"):
    check(x not in by_id, "%s should be excluded" % x)

# positive: banded from cvss
f101 = by_id["F-101"]
check(f101["category"] == "sql", "F-101 winner category should be sql")
check(f101["route"] == "/api/orders", "F-101 winner route wrong")
check(abs(f101["cvss"] - 9.1) < 1e-9, "F-101 cvss wrong")
check(f101["severity"] == "critical", "F-101 should band to critical")
check(f101["review_state"] == "ok", "F-101 review_state wrong")

# dedup: exactly one F-101
check(ids.count("F-101") == 1, "F-101 should be deduplicated to one element")

# null policy
f110 = by_id["F-110"]
check(f110["severity"] == "unknown", "F-110 severity should be unknown")
check(f110["review_state"] == "needs_review", "F-110 review_state wrong")
check(f110["cvss"] is None, "F-110 cvss should be null")

# banding medium
check(by_id["F-130"]["severity"] == "medium", "F-130 should be medium")

# content over filename: F-160 lives in file_audit.md but category field is sql
f160 = by_id["F-160"]
check(f160["category"] == "sql", "F-160 category should follow content (sql)")
check(f160["route"] == "/api/orders", "F-160 route wrong")
check(f160["severity"] == "medium", "F-160 should band to medium")

# dedup tiebreak: F-200 equal cvss -> auth wins over file
f200 = by_id["F-200"]
check(f200["category"] == "auth", "F-200 tiebreak should pick auth")
check(f200["route"] == "/api/orders", "F-200 winner route wrong")
check(f200["severity"] == "medium", "F-200 should band to medium")

# recompute coverage from fixtures
inv = [l.strip() for l in open(os.path.join(input_dir, "route_inventory.txt"), encoding="utf-8") if l.strip()]
total = len(inv)
froutes = {e["route"] for e in findings}
annotated = sum(1 for x in inv if x in froutes)
ratio = round(annotated / total, 4) if total else 0.0
cov = r["coverage"]
check(cov["total"] == total, "coverage.total wrong: %r vs %r" % (cov["total"], total))
check(cov["annotated"] == annotated, "coverage.annotated wrong: %r vs %r" % (cov["annotated"], annotated))
check(abs(cov["ratio"] - ratio) < 1e-9, "coverage.ratio wrong: %r vs %r" % (cov["ratio"], ratio))
check(cov["total"] == 5 and cov["annotated"] == 4, "expected annotated 4 / total 5")
check(abs(cov["ratio"] - 0.8) < 1e-9, "expected ratio 0.8")

# gate at boundary 0.80 PASS (instruction threshold beats project_rules 0.90)
check(r["gate"] == "PASS", "gate should PASS at ratio 0.80 (instruction 0.80 wins)")

# leakage checks
check("/Users/" not in raw, "leaked /Users/ path")
check("http://" not in raw and "https://" not in raw, "leaked URL scheme")
check(re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", raw) is None, "leaked IPv4")
check(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", raw) is None, "leaked @-token")

print("content assertions passed")
PY

# Determinism: run twice to temp outputs, compare findings ordering byte-for-byte
python3 /app/solution.py --input "$INPUT" --output /tmp/r1.json
python3 /app/solution.py --input "$INPUT" --output /tmp/r2.json
cmp -s /tmp/r1.json /tmp/r2.json || fail "output not deterministic"

# Gate boundary: build a temp package just below 0.80 (3/4 = 0.75 -> FAIL)
TB=$(mktemp -d)
mkdir -p "$TB/findings"
cat > "$TB/findings/sql_audit.md" <<'EOF'
### G-1
category: auth
cvss: 8.0
route: /r1
summary: x

### G-2
category: auth
cvss: 8.0
route: /r2
summary: x

### G-3
category: auth
cvss: 8.0
route: /r3
summary: x
EOF
cat > "$TB/revalidation.md" <<'EOF'
| id | status |
| --- | --- |
| G-1 | Confirmed |
| G-2 | Confirmed |
| G-3 | Confirmed |
EOF
printf '/r1\n/r2\n/r3\n/r4\n' > "$TB/route_inventory.txt"
cat > "$TB/project_rules.md" <<'EOF'
release_gate_threshold: 0.50
EOF
python3 /app/solution.py --input "$TB" --output "$TB/out.json"
python3 - "$TB/out.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
c = r["coverage"]
assert c["annotated"] == 3 and c["total"] == 4, ("boundary annotated/total", c)
assert abs(c["ratio"] - 0.75) < 1e-9, ("boundary ratio", c)
assert r["gate"] == "FAIL", ("just-below-0.80 must FAIL", r["gate"])
print("gate just-below boundary passed")
PY

# Gate boundary exactly 0.80 -> PASS
printf '/r1\n/r2\n/r3\n/r4\n/r5\n' > "$TB/route_inventory.txt"
cat > "$TB/findings/sql_audit.md" <<'EOF'
### G-1
category: auth
cvss: 8.0
route: /r1
summary: x

### G-2
category: auth
cvss: 8.0
route: /r2
summary: x

### G-3
category: auth
cvss: 8.0
route: /r3
summary: x

### G-4
category: auth
cvss: 8.0
route: /r4
summary: x
EOF
cat > "$TB/revalidation.md" <<'EOF'
| id | status |
| --- | --- |
| G-1 | Confirmed |
| G-2 | Confirmed |
| G-3 | Confirmed |
| G-4 | Confirmed |
EOF
python3 /app/solution.py --input "$TB" --output "$TB/out2.json"
python3 - "$TB/out2.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
c = r["coverage"]
assert c["annotated"] == 4 and c["total"] == 5, ("at-boundary annotated/total", c)
assert abs(c["ratio"] - 0.8) < 1e-9, ("at-boundary ratio", c)
assert r["gate"] == "PASS", ("exactly-0.80 must PASS", r["gate"])
print("gate at-boundary passed")
PY

rm -rf "$TB"
echo "ALL TESTS PASSED"
