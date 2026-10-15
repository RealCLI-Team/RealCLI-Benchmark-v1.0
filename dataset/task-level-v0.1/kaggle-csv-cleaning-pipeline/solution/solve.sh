#!/usr/bin/env bash
# Reference solution. Builds the reproducible cleaning pipeline under scripts/
# and runs it.
set -euo pipefail

# The solution bundle is mounted at /solution, but the task inputs live under
# the task workdir (/app per environment/Dockerfile). Resolve the task root
# explicitly instead of deriving it from this script's location.
if [[ -n "${TASK_ROOT:-}" && -f "${TASK_ROOT%/}/inputs/aia-xt1-dl-kaggle.zip" ]]; then
  ROOT="${TASK_ROOT%/}"
elif [[ -f /app/inputs/aia-xt1-dl-kaggle.zip ]]; then
  ROOT=/app
else
  ROOT="$(pwd)"
fi
cd "$ROOT"

mkdir -p scripts outputs

# ---------------------------------------------------------------------------
# 1. The pipeline implementation (pure Python stdlib, fully offline).
# ---------------------------------------------------------------------------
cat > scripts/clean_pipeline.py <<'PYEOF'
import csv, io, os, re, sys, zipfile, hashlib, html

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INPUT_ZIP = os.path.join(ROOT, "inputs", "aia-xt1-dl-kaggle.zip")
OUT = os.path.join(ROOT, "outputs")
os.makedirs(OUT, exist_ok=True)

REGION_MAP = {"north": "North", "south": "South", "east": "East", "west": "West"}

def decode_bytes(raw):
    """Best-effort decode: UTF-8 first, fall back to Latin-1."""
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("latin-1"), True

def norm_region(v):
    return REGION_MAP.get(v.strip().lower(), "")

def norm_date(v):
    v = v.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", v)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", v)
        if m:
            y, mo, d = m.groups()
        else:
            m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", v)  # DD/MM/YYYY
            if m:
                d, mo, y = m.groups()
            else:
                return None
    y, mo, d = int(y), int(mo), int(d)
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"

def norm_amount(v):
    s = v.strip().replace("$", "").replace(",", "")
    if s == "":
        return None
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return None

def read_csv_text(text):
    return list(csv.reader(io.StringIO(text)))

# --- load archive members --------------------------------------------------
with zipfile.ZipFile(INPUT_ZIP) as z:
    raw = {n: z.read(n) for n in ("customers.csv", "products.csv", "transactions.csv")}

rejected = []  # (source_file, raw_line, reason)

def raw_line(header, row):
    buf = io.StringIO()
    csv.writer(buf).writerow(row)
    return buf.getvalue().rstrip("\r\n")

# --- customers -------------------------------------------------------------
ctext, _ = decode_bytes(raw["customers.csv"])
crows = read_csv_text(ctext)
chead, cbody = crows[0], crows[1:]
customers_clean = []
for row in cbody:
    rec = dict(zip(chead, row))
    name = rec.get("name", "").strip()
    region = norm_region(rec.get("region", ""))
    date = norm_date(rec.get("signup_date", ""))
    if not name:
        rejected.append(("customers.csv", raw_line(chead, row), "missing_name")); continue
    if not region:
        rejected.append(("customers.csv", raw_line(chead, row), "missing_region")); continue
    if date is None:
        rejected.append(("customers.csv", raw_line(chead, row), "invalid_date")); continue
    customers_clean.append([rec["customer_id"], name, region, date, rec.get("email", "").strip()])

# --- products --------------------------------------------------------------
ptext, _ = decode_bytes(raw["products.csv"])
prows = read_csv_text(ptext)
phead, pbody = prows[0], prows[1:]
products_clean = []
for row in pbody:
    rec = dict(zip(phead, row))
    pname = rec.get("product_name", "").strip()
    try:
        price = float(rec.get("unit_price", "").strip())
    except ValueError:
        price = None
    if not pname:
        rejected.append(("products.csv", raw_line(phead, row), "missing_name")); continue
    if price is None:
        rejected.append(("products.csv", raw_line(phead, row), "invalid_amount")); continue
    if price < 0:
        rejected.append(("products.csv", raw_line(phead, row), "negative_price")); continue
    products_clean.append([rec["product_id"], pname, rec.get("category", "").strip(), f"{price:.2f}"])

cust_ids = {r[0] for r in customers_clean}
prod_ids = {r[0] for r in products_clean}

# --- transactions ----------------------------------------------------------
ttext, _ = decode_bytes(raw["transactions.csv"])
trows = read_csv_text(ttext)
thead, tbody = trows[0], trows[1:]
txn_clean = []
for row in tbody:
    rec = dict(zip(thead, row))
    date = norm_date(rec.get("txn_date", ""))
    amount = norm_amount(rec.get("amount", ""))
    cid = rec.get("customer_id", "").strip()
    pid = rec.get("product_id", "").strip()
    if date is None:
        rejected.append(("transactions.csv", raw_line(thead, row), "invalid_date")); continue
    if amount is None:
        rejected.append(("transactions.csv", raw_line(thead, row), "invalid_amount")); continue
    if cid not in cust_ids:
        rejected.append(("transactions.csv", raw_line(thead, row), "unknown_customer")); continue
    if pid not in prod_ids:
        rejected.append(("transactions.csv", raw_line(thead, row), "unknown_product")); continue
    txn_clean.append([rec["transaction_id"], cid, pid, amount, rec.get("currency", "").strip(), date])

# --- write cleaned tables --------------------------------------------------
def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

write_csv(os.path.join(OUT, "customers_cleaned.csv"),
          ["customer_id", "name", "region", "signup_date", "email"], customers_clean)
write_csv(os.path.join(OUT, "transactions_cleaned.csv"),
          ["transaction_id", "customer_id", "product_id", "amount", "currency", "txn_date"], txn_clean)
write_csv(os.path.join(OUT, "products_cleaned.csv"),
          ["product_id", "product_name", "category", "unit_price"], products_clean)
write_csv(os.path.join(OUT, "rejected_records.csv"),
          ["source_file", "raw_line", "reason"], rejected)

# --- aggregation (lightweight join) ---------------------------------------
cust_region = {r[0]: r[2] for r in customers_clean}
prod_cat = {r[0]: r[2] for r in products_clean}
agg = {}
for t in txn_clean:
    _, cid, pid, amount, _, _ = t
    key = (prod_cat[pid], cust_region[cid])
    cnt, total = agg.get(key, (0, 0.0))
    agg[key] = (cnt + 1, total + float(amount))
agg_rows = [[cat, reg, str(cnt), f"{total:.2f}"]
            for (cat, reg), (cnt, total) in sorted(agg.items())]
write_csv(os.path.join(OUT, "aggregated_metrics.csv"),
          ["category", "region", "txn_count", "total_amount"], agg_rows)

# --- dimensions audit ------------------------------------------------------
reason_counts = {}
for _, _, reason in rejected:
    reason_counts[reason] = reason_counts.get(reason, 0) + 1

audit = []
audit.append("=== dimensions audit ===")
specs = [
    ("customers.csv", len(cbody), len(customers_clean), 5),
    ("products.csv", len(pbody), len(products_clean), 4),
    ("transactions.csv", len(tbody), len(txn_clean), 6),
]
for name, before, after, cols in specs:
    audit.append(f"{name}: rows_before={before} rows_after={after} cols={cols} dropped={before - after}")
audit.append("")
audit.append("=== rejection reasons ===")
for reason in sorted(reason_counts):
    audit.append(f"{reason}: {reason_counts[reason]}")
audit.append(f"total_rejected: {len(rejected)}")
with open(os.path.join(OUT, "dimensions_audit.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(audit) + "\n")

# --- missing value distribution (for the report) --------------------------
def missing_stats(header, body):
    stats = {h: 0 for h in header}
    for row in body:
        rec = dict(zip(header, row))
        for h in header:
            if rec.get(h, "").strip() == "":
                stats[h] += 1
    return stats

miss_cust = missing_stats(chead, cbody)
miss_prod = missing_stats(phead, pbody)
miss_txn = missing_stats(thead, tbody)

# --- quality score ---------------------------------------------------------
def score(before, after):
    return 0.0 if before == 0 else round(100.0 * after / before, 1)

q_rows = [
    ("customers.csv", len(cbody), len(customers_clean), score(len(cbody), len(customers_clean))),
    ("products.csv", len(pbody), len(products_clean), score(len(pbody), len(products_clean))),
    ("transactions.csv", len(tbody), len(txn_clean), score(len(tbody), len(txn_clean))),
]

# --- offline HTML report ---------------------------------------------------
def esc(x):
    return html.escape(str(x))

def miss_table(title, stats):
    rows = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in stats.items())
    return f"<h3>{esc(title)}</h3><table><tr><th>列</th><th>缺失值数量</th></tr>{rows}</table>"

agg_html = "".join(
    f"<tr><td>{esc(r[0])}</td><td>{esc(r[1])}</td><td>{esc(r[2])}</td><td>{esc(r[3])}</td></tr>"
    for r in agg_rows)
q_html = "".join(
    f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td><td>{esc(d)}</td></tr>"
    for (a, b, c, d) in q_rows)

html_doc = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>数据质量报告 — aia-xt1-dl-kaggle</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; color: #222; background: #fafafa; }}
h1 {{ border-bottom: 3px solid #4a7; padding-bottom: .3rem; }}
h2 {{ color: #2a6; margin-top: 2rem; }}
table {{ border-collapse: collapse; margin: .6rem 0; }}
th, td {{ border: 1px solid #bbb; padding: 4px 10px; text-align: left; }}
th {{ background: #e8f4ee; }}
.bar {{ background: #4a7; height: 14px; display: inline-block; }}
</style>
</head>
<body>
<h1>数据质量报告</h1>
<p>来源归档：inputs/aia-xt1-dl-kaggle.zip（离线生成，无外部依赖）。</p>

<h2>缺失值分布</h2>
{miss_table("customers.csv", miss_cust)}
{miss_table("products.csv", miss_prod)}
{miss_table("transactions.csv", miss_txn)}

<h2>分组聚合统计</h2>
<table>
<tr><th>category</th><th>region</th><th>txn_count</th><th>total_amount</th></tr>
{agg_html}
</table>

<h2>质量评分</h2>
<table>
<tr><th>文件</th><th>清洗前行数</th><th>清洗后行数</th><th>保留率(%)</th></tr>
{q_html}
</table>
</body>
</html>
"""
with open(os.path.join(OUT, "quality_report.html"), "w", encoding="utf-8") as f:
    f.write(html_doc)

# --- manifest.md5 ----------------------------------------------------------
manifest_targets = [
    "inputs/aia-xt1-dl-kaggle.zip",
    "outputs/customers_cleaned.csv",
    "outputs/transactions_cleaned.csv",
    "outputs/products_cleaned.csv",
    "outputs/rejected_records.csv",
    "outputs/aggregated_metrics.csv",
    "outputs/quality_report.html",
    "outputs/dimensions_audit.txt",
]
lines = []
for rel in manifest_targets:
    h = hashlib.md5(open(os.path.join(ROOT, rel), "rb").read()).hexdigest()
    lines.append(f"{h}  {rel}")
with open(os.path.join(OUT, "manifest.md5"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print("pipeline complete:",
      f"customers={len(customers_clean)} products={len(products_clean)} "
      f"transactions={len(txn_clean)} rejected={len(rejected)} groups={len(agg_rows)}")
PYEOF

# ---------------------------------------------------------------------------
# 2. The reproducible runner (a required deliverable).
# ---------------------------------------------------------------------------
cat > scripts/run_pipeline.sh <<'RUNEOF'
#!/usr/bin/env bash
# Reproducible offline cleaning pipeline runner.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python3 scripts/clean_pipeline.py
RUNEOF
chmod +x scripts/run_pipeline.sh

# ---------------------------------------------------------------------------
# 3. Run it.
# ---------------------------------------------------------------------------
bash scripts/run_pipeline.sh

echo "solve.sh done."
