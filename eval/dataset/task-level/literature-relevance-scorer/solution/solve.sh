#!/usr/bin/env bash
set -euo pipefail

cd /app

cat > /app/solution.py <<'PY'
import argparse
import glob
import json
import os
from decimal import Decimal, ROUND_HALF_UP


def is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def load_rubric(input_dir):
    with open(os.path.join(input_dir, "rubric.json"), encoding="utf-8") as f:
        return json.load(f)


def read_records(input_dir):
    papers_dir = os.path.join(input_dir, "papers")
    records = []
    seq = 0
    for path in sorted(glob.glob(os.path.join(papers_dir, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                records.append((seq, os.path.basename(path), line_no, obj))
                seq += 1
    return records


def round_half_up_1(value):
    return float(Decimal(str(round(value, 6))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def score_record(obj, rubric):
    title = obj.get("title") or ""
    abstract = obj.get("abstract")
    abstract_missing = is_blank(abstract)
    abstract_used = "" if abstract_missing else str(abstract)
    text = (str(title) + "\n" + abstract_used).lower()

    raw = float(rubric["base_score"])
    cats = set()
    for kw in rubric["keywords"]:
        if kw["term"].lower() in text:
            raw += float(kw["weight"])
            cats.add(kw["category"])
    raw = max(float(rubric["min_score"]), min(float(rubric["max_score"]), raw))
    score = round_half_up_1(raw)
    matched_categories = sorted(cats)

    subtopic = ""
    if score >= float(rubric["subtopic_score_threshold"]):
        best_label = None
        best_count = 0
        for st in rubric["subtopics"]:
            count = sum(1 for t in st["terms"] if t.lower() in text)
            if count > best_count:
                best_count = count
                best_label = st["label"]
        subtopic = best_label if best_count > 0 else rubric["default_subtopic"]

    return {
        "score": score,
        "subtopic": subtopic,
        "matched_categories": matched_categories,
        "abstract_missing": abstract_missing,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    rubric = load_rubric(args.input)
    records = read_records(args.input)

    valid_by_doi = {}
    errors = []

    for seq, fname, line_no, obj in records:
        doi = obj.get("doi")
        if is_blank(doi):
            errors.append((seq, {
                "doi": "" if doi is None else str(doi),
                "score": None,
                "subtopic": "",
                "matched_categories": [],
                "abstract_missing": False,
                "status": "error",
                "reason": "missing_doi",
            }))
            continue
        title = obj.get("title")
        if is_blank(title):
            errors.append((seq, {
                "doi": str(doi),
                "score": None,
                "subtopic": "",
                "matched_categories": [],
                "abstract_missing": False,
                "status": "error",
                "reason": "missing_title",
            }))
            continue

        abstract = obj.get("abstract")
        abs_len = len(abstract.strip()) if isinstance(abstract, str) else 0
        key = str(doi)
        prev = valid_by_doi.get(key)
        if prev is None or abs_len > prev["abs_len"] or (abs_len == prev["abs_len"] and seq < prev["seq"]):
            valid_by_doi[key] = {"seq": seq, "abs_len": abs_len, "obj": obj, "doi": key}

    valid_rows = []
    for key, info in valid_by_doi.items():
        computed = score_record(info["obj"], rubric)
        valid_rows.append({
            "doi": info["doi"],
            "score": computed["score"],
            "subtopic": computed["subtopic"],
            "matched_categories": computed["matched_categories"],
            "abstract_missing": computed["abstract_missing"],
            "status": "ok",
            "reason": "",
        })

    valid_rows.sort(key=lambda r: (-r["score"], r["doi"]))
    errors.sort(key=lambda e: e[0])

    out_rows = valid_rows + [e[1] for e in errors]

    with open(args.output, "w", encoding="utf-8") as f:
        for row in out_rows:
            ordered = {
                "doi": row["doi"],
                "score": row["score"],
                "subtopic": row["subtopic"],
                "matched_categories": row["matched_categories"],
                "abstract_missing": row["abstract_missing"],
                "status": row["status"],
                "reason": row["reason"],
            }
            f.write(json.dumps(ordered, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
PY

python /app/solution.py --input /app/inputs --output /app/result.jsonl
