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
set -euo pipefail

RESULT="/app/result.jsonl"
INPUT="/app/inputs"

if [ ! -f "$RESULT" ]; then
  echo "FAIL: $RESULT not found"
  exit 1
fi

python3 - "$RESULT" "$INPUT" <<'PY'
import glob
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

result_path, input_dir = sys.argv[1], sys.argv[2]


def is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def rhu1(value):
    return float(Decimal(str(round(value, 6))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


with open(os.path.join(input_dir, "rubric.json"), encoding="utf-8") as f:
    rubric = json.load(f)

records = []
seq = 0
for path in sorted(glob.glob(os.path.join(input_dir, "papers", "*.jsonl"))):
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            records.append((seq, os.path.basename(path), ln, json.loads(line)))
            seq += 1


def compute(obj):
    title = obj.get("title") or ""
    abstract = obj.get("abstract")
    missing = is_blank(abstract)
    used = "" if missing else str(abstract)
    text = (str(title) + "\n" + used).lower()
    raw = float(rubric["base_score"])
    cats = set()
    for kw in rubric["keywords"]:
        if kw["term"].lower() in text:
            raw += float(kw["weight"])
            cats.add(kw["category"])
    raw = max(float(rubric["min_score"]), min(float(rubric["max_score"]), raw))
    score = rhu1(raw)
    sub = ""
    if score >= float(rubric["subtopic_score_threshold"]):
        best, bc = None, 0
        for st in rubric["subtopics"]:
            c = sum(1 for t in st["terms"] if t.lower() in text)
            if c > bc:
                bc, best = c, st["label"]
        sub = best if bc > 0 else rubric["default_subtopic"]
    return score, sub, sorted(cats), missing


valid = {}
errors = []
for s, fn, ln, obj in records:
    doi = obj.get("doi")
    if is_blank(doi):
        errors.append((s, {"doi": "" if doi is None else str(doi), "score": None, "subtopic": "",
                           "matched_categories": [], "abstract_missing": False,
                           "status": "error", "reason": "missing_doi"}))
        continue
    if is_blank(obj.get("title")):
        errors.append((s, {"doi": str(doi), "score": None, "subtopic": "",
                           "matched_categories": [], "abstract_missing": False,
                           "status": "error", "reason": "missing_title"}))
        continue
    a = obj.get("abstract")
    al = len(a.strip()) if isinstance(a, str) else 0
    k = str(doi)
    p = valid.get(k)
    if p is None or al > p["al"] or (al == p["al"] and s < p["s"]):
        valid[k] = {"s": s, "al": al, "obj": obj, "doi": k}

vrows = []
for k, info in valid.items():
    sc, sub, cats, miss = compute(info["obj"])
    vrows.append({"doi": info["doi"], "score": sc, "subtopic": sub,
                  "matched_categories": cats, "abstract_missing": miss,
                  "status": "ok", "reason": ""})
vrows.sort(key=lambda r: (-r["score"], r["doi"]))
errors.sort(key=lambda e: e[0])
expected = vrows + [e[1] for e in errors]

with open(result_path, encoding="utf-8") as f:
    actual = [json.loads(l) for l in f if l.strip()]

fails = []

expected_keys = {"doi", "score", "subtopic", "matched_categories",
                 "abstract_missing", "status", "reason"}
for i, row in enumerate(actual):
    if set(row.keys()) != expected_keys:
        fails.append(f"row {i}: key set mismatch: {sorted(row.keys())}")

if len(actual) != len(expected):
    fails.append(f"row count mismatch: expected {len(expected)}, got {len(actual)}")

for i, (e, a) in enumerate(zip(expected, actual)):
    if a.get("doi") != e["doi"]:
        fails.append(f"row {i}: doi expected {e['doi']} got {a.get('doi')}")
    if e["score"] is None:
        if a.get("score") is not None:
            fails.append(f"row {i}: score expected null got {a.get('score')}")
    else:
        av = a.get("score")
        if not isinstance(av, (int, float)) or isinstance(av, bool):
            fails.append(f"row {i}: score not numeric: {av}")
        elif abs(float(av) - e["score"]) > 1e-9:
            fails.append(f"row {i}: score expected {e['score']} got {av}")
        else:
            if round(float(av), 1) != float(av):
                fails.append(f"row {i}: score not 1 decimal: {av}")
    for key in ("subtopic", "matched_categories", "abstract_missing", "status", "reason"):
        if a.get(key) != e[key]:
            fails.append(f"row {i}: {key} expected {e[key]!r} got {a.get(key)!r}")

# targeted assertions
by_doi = {r.get("doi"): r for r in actual}

# boundary round-half-up: a2 raw 4.95 -> 5.0, triggers subtopic
if "10.1000/a2" in by_doi:
    r = by_doi["10.1000/a2"]
    if r["score"] != 5.0:
        fails.append(f"a2 boundary score expected 5.0 got {r['score']}")
    if r["subtopic"] != "风险识别与评估":
        fails.append(f"a2 subtopic expected 风险识别与评估 got {r['subtopic']}")
    if r["abstract_missing"] is not True:
        fails.append("a2 abstract_missing expected true")

# just below threshold: a3 -> 4.9, subtopic empty
if "10.1000/a3" in by_doi:
    r = by_doi["10.1000/a3"]
    if r["score"] != 4.0:
        fails.append(f"a3 expected 4.0 got {r['score']}")
    if r["subtopic"] != "":
        fails.append(f"a3 subtopic expected empty got {r['subtopic']}")

# clamp + dedup winner (longer abstract from misc): a1 -> 10.0
if "10.1000/a1" in by_doi:
    r = by_doi["10.1000/a1"]
    if r["score"] != 10.0:
        fails.append(f"a1 expected clamp 10.0 got {r['score']}")

# dedup: exactly one a1
if sum(1 for r in actual if r.get("doi") == "10.1000/a1") != 1:
    fails.append("a1 not deduplicated to single row")

# near-duplicate title different doi kept separately
if "10.1000/a7" not in by_doi:
    fails.append("a7 (near-duplicate title, distinct doi) missing")

# default_subtopic when score>=threshold but no subtopic term hit
if "10.1000/a10" in by_doi:
    r = by_doi["10.1000/a10"]
    if r["subtopic"] != "综述与框架":
        fails.append(f"a10 subtopic expected 综述与框架 got {r['subtopic']}")
    if r["score"] != 8.5:
        fails.append(f"a10 expected 8.5 got {r['score']}")

# null abstract still scored
if "10.1000/a8" in by_doi:
    r = by_doi["10.1000/a8"]
    if r["abstract_missing"] is not True or r["status"] != "ok":
        fails.append("a8 null-abstract handling wrong")

# error records
err = [r for r in actual if r.get("status") == "error"]
reasons = sorted(r["reason"] for r in err)
if reasons != ["missing_doi", "missing_title"]:
    fails.append(f"error reasons expected [missing_doi, missing_title] got {reasons}")
for r in err:
    if r["score"] is not None or r["subtopic"] != "" or r["matched_categories"] != []:
        fails.append("error record fields not reset")

# errors after all valid
statuses = [r["status"] for r in actual]
if "ok" in statuses and "error" in statuses:
    if statuses.index("error") < len(vrows):
        pass
    last_ok = max(i for i, s in enumerate(statuses) if s == "ok")
    first_err = min(i for i, s in enumerate(statuses) if s == "error")
    if first_err < last_ok:
        fails.append("error rows not placed after valid rows")

# no leaked private paths / urls
raw = open(result_path, encoding="utf-8").read()
for bad in ("http://", "https://", "/Users/", "C:\\", "/home/"):
    if bad in raw:
        fails.append(f"output leaked forbidden fragment: {bad}")

if fails:
    print("FAIL")
    for x in fails:
        print(" -", x)
    sys.exit(1)

print("PASS: all assertions passed")
PY
