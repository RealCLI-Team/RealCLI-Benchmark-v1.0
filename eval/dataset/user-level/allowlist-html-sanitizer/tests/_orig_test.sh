#!/usr/bin/env bash
set -euo pipefail

REPORT=/app/result.json
PAGES=/app/inputs/pages

if [ ! -f "$REPORT" ]; then
  echo "FAIL: $REPORT does not exist"
  exit 1
fi

python3 - <<'PY'
import json, os, re, sys, glob, shutil, subprocess

REPORT = "/app/result.json"
PAGES = "/app/inputs/pages"

errors = []

def check(cond, msg):
    if not cond:
        errors.append(msg)

with open(REPORT) as f:
    report = json.load(f)

check(isinstance(report, dict) and "files" in report, "report must be object with 'files'")
files = report.get("files", [])
check(isinstance(files, list), "'files' must be a list")

# 1. ordering ascending by name
names = [e["name"] for e in files]
check(names == sorted(names), "files must be sorted ascending by name, got %r" % names)

expected_files = [
    "benign.html", "empty.html", "legit_code.html",
    "malicious_link.html", "malicious_script.html", "obfuscated.html",
]
check(names == expected_files, "unexpected file set/order: %r" % names)

by_name = {e["name"]: e for e in files}

# every entry has required schema
for e in files:
    for k in ("name", "status", "removed_tags", "stripped_attributes", "blocked_urls"):
        check(k in e, "entry %r missing key %s" % (e.get("name"), k))
    check(e["status"] in ("sanitized", "unchanged", "error"),
          "bad status for %s: %r" % (e["name"], e.get("status")))

# 2. expected statuses per fixture
expected_status = {
    "benign.html": "unchanged",
    "empty.html": "unchanged",
    "legit_code.html": "unchanged",
    "malicious_link.html": "sanitized",
    "malicious_script.html": "sanitized",
    "obfuscated.html": "sanitized",
}
for name, st in expected_status.items():
    check(by_name.get(name, {}).get("status") == st,
          "status[%s] expected %s got %r" % (name, st, by_name.get(name, {}).get("status")))

# at least one sanitized and one unchanged
statuses = [e["status"] for e in files]
check("sanitized" in statuses, "expected at least one 'sanitized'")
check("unchanged" in statuses, "expected at least one 'unchanged'")

# 3. count fields for sanitized fixtures
ms = by_name.get("malicious_script.html", {})
check(ms.get("removed_tags", 0) >= 2, "malicious_script removed_tags >= 2, got %r" % ms.get("removed_tags"))
check(ms.get("stripped_attributes", 0) >= 1, "malicious_script stripped_attributes >= 1, got %r" % ms.get("stripped_attributes"))

ml = by_name.get("malicious_link.html", {})
check(ml.get("blocked_urls", 0) >= 2, "malicious_link blocked_urls >= 2, got %r" % ml.get("blocked_urls"))

ob = by_name.get("obfuscated.html", {})
check(ob.get("blocked_urls", 0) >= 2, "obfuscated blocked_urls >= 2, got %r" % ob.get("blocked_urls"))

# helpers reading sanitized files
def read(name):
    with open(os.path.join(PAGES, name), encoding="utf-8") as f:
        return f.read()

on_attr = re.compile(r"on[a-z]+\s*=", re.IGNORECASE)
blocked_scheme = re.compile(r'(href|src)\s*=\s*"\s*(javascript:|vbscript:|data:text/html)', re.IGNORECASE)

# 4. malicious files: structural assertions
for name in ("malicious_script.html", "malicious_link.html", "obfuscated.html"):
    content = read(name)
    low = content.lower()
    check("<script" not in low, "%s still contains <script" % name)
    check(on_attr.search(content) is None, "%s still contains an on*= event attribute" % name)
    check(blocked_scheme.search(content) is None, "%s still has href/src with blocked scheme" % name)

# malicious_script: marquee unwrapped but text kept
ms_content = read("malicious_script.html")
check("<marquee" not in ms_content.lower(), "malicious_script <marquee> tag not removed")
check("scrolling banner" in ms_content, "malicious_script lost unwrapped text 'scrolling banner'")

# obfuscated: data:image kept (not blocked)
ob_content = read("obfuscated.html")
check("data:image/png" in ob_content, "obfuscated dropped a legitimate data:image src")

# 5. legit_code: text preserved, element not deleted
lc = read("legit_code.html")
check("javascript:void(0)" in lc, "legit_code lost literal 'javascript:void(0)' text")
check("<code>" in lc.lower(), "legit_code <code> element was deleted")
check("onclick handlers are dangerous" in lc, "legit_code lost link text")

# 6. benign preserved (normalized-level markup intact)
bn = read("benign.html")
check("<strong>world</strong>" in bn, "benign lost <strong>world</strong>")
check('href="https://example.test/page"' in bn, "benign lost legitimate href")
check('title="Home"' in bn, "benign lost legitimate title attribute")

# empty stays empty
check(read("empty.html") == "", "empty.html should remain empty")

# 7. determinism: run twice on identical fresh copies -> identical report
d1, d2 = "/app/.det1", "/app/.det2"
for d in (d1, d2):
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for p in glob.glob(os.path.join(PAGES, "*.html")):
        shutil.copy(p, d)
subprocess.check_call(["python3", "/app/solution.py", "--input", d1, "--output", "/app/.r1.json"])
subprocess.check_call(["python3", "/app/solution.py", "--input", d2, "--output", "/app/.r2.json"])
with open("/app/.r1.json") as f: r1 = f.read()
with open("/app/.r2.json") as f: r2 = f.read()
check(r1 == r2, "report is not deterministic across runs")

if errors:
    print("TESTS FAILED:")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("ALL TESTS PASSED")
PY
