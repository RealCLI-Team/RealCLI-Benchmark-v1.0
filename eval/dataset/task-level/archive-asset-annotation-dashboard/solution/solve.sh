#!/usr/bin/env bash
# 资产标注与体检：解压归档 -> 类型标注/重复异常检测 -> CSV/JSON/离线HTML
set -euo pipefail

ROOT="$(pwd)"
ZIP="${ROOT}/inputs/2023zlablearning.zip"
WORK="${ROOT}/extracted"

rm -rf "${WORK}"
mkdir -p "${WORK}"
unzip -q -o "${ZIP}" -d "${WORK}"

python3 - "$WORK" "$ROOT" << 'PYEOF'
import sys, os, csv, json, hashlib, html

work = sys.argv[1]
root = sys.argv[2]

EXT_MAP = {
    ".csv": "csv", ".json": "json", ".txt": "txt",
    ".md": "md", ".log": "log", ".xml": "xml",
}

# collect regular files, relative paths with forward slashes
records = []
for dirpath, dirnames, filenames in os.walk(work):
    for fn in filenames:
        full = os.path.join(dirpath, fn)
        rel = os.path.relpath(full, work).replace(os.sep, "/")
        records.append((rel, full))

records.sort(key=lambda x: x[0])

seen_hashes = set()
rows = []
for rel, full in records:
    size = os.path.getsize(full)
    ext = os.path.splitext(rel)[1].lower()
    type_tag = EXT_MAP.get(ext, "unknown")

    if size == 0:
        status = "anomaly"
    else:
        with open(full, "rb") as fh:
            digest = hashlib.md5(fh.read()).hexdigest()
        if digest in seen_hashes:
            status = "duplicate"
        else:
            seen_hashes.add(digest)
            if type_tag == "unknown":
                status = "unknown"
            else:
                status = "normal"
    rows.append({
        "file_path": rel,
        "type_tag": type_tag,
        "size_bytes": size,
        "status": status,
    })

# annotated_inventory.csv
csv_path = os.path.join(root, "annotated_inventory.csv")
with open(csv_path, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["file_path", "type_tag", "size_bytes", "status"])
    w.writeheader()
    for r in rows:
        w.writerow(r)

# validation_report.json
total = len(rows)
dup = sum(1 for r in rows if r["status"] == "duplicate")
anom = sum(1 for r in rows if r["status"] == "anomaly")
type_dist = {}
for r in rows:
    type_dist[r["type_tag"]] = type_dist.get(r["type_tag"], 0) + 1
status_dist = {}
for r in rows:
    status_dist[r["status"]] = status_dist.get(r["status"], 0) + 1

report = {
    "total_files": total,
    "duplicate_count": dup,
    "anomaly_count": anom,
    "type_distribution": type_dist,
    "status_distribution": status_dist,
}
with open(os.path.join(root, "validation_report.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=2)

# dashboard.html (fully offline, inline css/js)
anomalies = [r for r in rows if r["status"] == "anomaly"]
max_count = max(type_dist.values()) if type_dist else 1

bars = []
for tag in sorted(type_dist):
    cnt = type_dist[tag]
    pct = int(round(cnt / max_count * 100)) if max_count else 0
    bars.append(
        '<div class="bar-row">'
        '<span class="bar-label">{label}</span>'
        '<span class="bar-track"><span class="bar-fill" style="width:{pct}%"></span></span>'
        '<span class="bar-val">{cnt}</span>'
        '</div>'.format(label=html.escape(tag), pct=pct, cnt=cnt)
    )
bars_html = "\n".join(bars)

if anomalies:
    arows = "\n".join(
        "<tr><td>{}</td><td>{}</td></tr>".format(
            html.escape(r["file_path"]), r["size_bytes"]
        )
        for r in anomalies
    )
else:
    arows = '<tr><td colspan="2">无异常文件</td></tr>'

doc = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>2023 Z-Lab 归档资产标注看板</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Arial, sans-serif; margin: 24px; color:#1f2933; background:#f5f7fa; }}
h1 {{ font-size: 22px; }}
.cards {{ display:flex; gap:16px; margin:16px 0; }}
.card {{ background:#fff; border-radius:10px; padding:16px 20px; box-shadow:0 1px 3px rgba(0,0,0,.1); }}
.card .num {{ font-size:28px; font-weight:700; }}
.card .lbl {{ color:#627d98; font-size:13px; }}
.section {{ background:#fff; border-radius:10px; padding:16px 20px; margin:16px 0; box-shadow:0 1px 3px rgba(0,0,0,.1); }}
.bar-row {{ display:flex; align-items:center; margin:6px 0; }}
.bar-label {{ width:90px; font-size:13px; }}
.bar-track {{ flex:1; background:#e4e7eb; border-radius:4px; height:18px; margin:0 8px; overflow:hidden; }}
.bar-fill {{ display:block; height:100%; background:#3e8ed0; }}
.bar-val {{ width:36px; text-align:right; font-size:13px; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ border:1px solid #d9e2ec; padding:6px 10px; text-align:left; font-size:13px; }}
th {{ background:#f0f4f8; }}
</style>
</head>
<body>
<h1>2023 Z-Lab 归档资产标注看板</h1>
<div class="cards">
  <div class="card"><div class="num">{total}</div><div class="lbl">总文件数</div></div>
  <div class="card"><div class="num">{dup}</div><div class="lbl">重复文件数</div></div>
  <div class="card"><div class="num">{anom}</div><div class="lbl">异常文件数</div></div>
</div>
<div class="section">
  <h2>文件类型分布</h2>
  {bars}
</div>
<div class="section">
  <h2>异常文件明细</h2>
  <table>
    <thead><tr><th>file_path</th><th>size_bytes</th></tr></thead>
    <tbody>
    {arows}
    </tbody>
  </table>
</div>
</body>
</html>
""".format(total=total, dup=dup, anom=anom, bars=bars_html, arows=arows)

with open(os.path.join(root, "dashboard.html"), "w", encoding="utf-8") as fh:
    fh.write(doc)

print("done: total={} dup={} anom={}".format(total, dup, anom))
print("type_distribution:", type_dist)
PYEOF
