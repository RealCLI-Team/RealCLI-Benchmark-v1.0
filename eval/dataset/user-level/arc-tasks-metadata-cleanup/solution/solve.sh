#!/usr/bin/env bash
set -euo pipefail

# 任务镜像 WORKDIR 为 /app，inputs 在镜像内固定位于 /app/inputs；
# solution/ 挂载到 /solution，不要依赖脚本相对路径推导任务根目录。
TASK_ROOT="/app"
cd "$TASK_ROOT"

mkdir -p "$TASK_ROOT/outputs"

python3 <<'PY'
import csv
import io
import json
import os
import zipfile
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import base64

task_root = Path(os.getcwd())
zip_path = task_root / "inputs" / "data" / "abstraction-and-reasoning-challenge.zip"
out_dir = task_root / "outputs"
out_dir.mkdir(exist_ok=True)

SUBSETS = {"training", "evaluation", "test"}

def validate_grid(g):
    if not isinstance(g, list) or len(g) == 0:
        return False, "grid_not_2d_int"
    row_len = None
    for row in g:
        if not isinstance(row, list) or len(row) == 0:
            return False, "grid_not_2d_int"
        if row_len is None:
            row_len = len(row)
        elif len(row) != row_len:
            return False, "grid_not_2d_int"
        for v in row:
            if not isinstance(v, int) or isinstance(v, bool):
                return False, "grid_not_2d_int"
            if v < 0 or v > 9:
                return False, "color_out_of_range"
    return True, None

rows = []
files_scanned = 0
files_valid = 0
files_dropped = 0
dropped_reasons = defaultdict(int)

with zipfile.ZipFile(zip_path, "r") as zf:
    for name in zf.namelist():
        if not name.endswith(".json"):
            continue
        parts = name.split("/")
        subset = None
        for p in parts:
            if p in SUBSETS:
                subset = p
                break
        if subset is None:
            continue
        files_scanned += 1
        try:
            data = json.loads(zf.read(name).decode("utf-8"))
        except Exception:
            files_dropped += 1
            dropped_reasons["json_parse_error"] += 1
            continue
        if not isinstance(data, dict) or "train" not in data or "test" not in data:
            files_dropped += 1
            dropped_reasons["missing_train_or_test"] += 1
            continue
        train = data["train"]
        test = data["test"]
        if not isinstance(train, list) or not isinstance(test, list):
            files_dropped += 1
            dropped_reasons["missing_train_or_test"] += 1
            continue
        bad = False
        reason = None
        colors = set()
        max_ih = max_iw = max_oh = max_ow = 0
        for sample in list(train) + list(test):
            if not isinstance(sample, dict) or "input" not in sample or "output" not in sample:
                bad = True
                reason = "sample_missing_io"
                break
            for key, is_out in (("input", False), ("output", True)):
                g = sample[key]
                ok, why = validate_grid(g)
                if not ok:
                    bad = True
                    reason = why
                    break
                h = len(g)
                w = len(g[0])
                if is_out:
                    max_oh = max(max_oh, h)
                    max_ow = max(max_ow, w)
                else:
                    max_ih = max(max_ih, h)
                    max_iw = max(max_iw, w)
                for row in g:
                    colors.update(row)
            if bad:
                break
        if bad:
            files_dropped += 1
            dropped_reasons[reason] += 1
            continue
        task_id = Path(name).name[:-5]
        rows.append({
            "task_id": task_id,
            "subset": subset,
            "n_train": len(train),
            "n_test": len(test),
            "max_input_h": max_ih,
            "max_input_w": max_iw,
            "max_output_h": max_oh,
            "max_output_w": max_ow,
            "n_colors": len(colors),
        })
        files_valid += 1

header = ["task_id","subset","n_train","n_test","max_input_h","max_input_w","max_output_h","max_output_w","n_colors"]
with open(out_dir / "tasks_clean.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=header)
    w.writeheader()
    for r in rows:
        w.writerow(r)

report = {
    "files_scanned": files_scanned,
    "files_valid": files_valid,
    "files_dropped": files_dropped,
    "dropped_reasons": dict(dropped_reasons),
}
with open(out_dir / "cleaning_report.json", "w") as f:
    json.dump(report, f, indent=2)

# subset summary
by_subset = defaultdict(list)
for r in rows:
    by_subset[r["subset"]].append(r)

summary_rows = []
for subset, items in by_subset.items():
    n = len(items)
    avg_train = sum(x["n_train"] for x in items) / n if n else 0.0
    avg_colors = sum(x["n_colors"] for x in items) / n if n else 0.0
    summary_rows.append({
        "subset": subset,
        "task_count": n,
        "avg_n_train": round(avg_train, 2),
        "avg_n_colors": round(avg_colors, 2),
    })
summary_rows.sort(key=lambda x: x["subset"])

with open(out_dir / "subset_summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["subset","task_count","avg_n_train","avg_n_colors"])
    w.writeheader()
    for r in summary_rows:
        w.writerow(r)

# HTML report with embedded PNG chart
fig, ax = plt.subplots(figsize=(6,4))
subsets_list = [r["subset"] for r in summary_rows]
counts = [r["task_count"] for r in summary_rows]
ax.bar(subsets_list, counts, color=["#4a90e2","#7ed321","#f5a623"][:len(subsets_list)])
ax.set_title("ARC Task Count by Subset")
ax.set_ylabel("task_count")
buf = io.BytesIO()
fig.tight_layout()
fig.savefig(buf, format="png")
plt.close(fig)
png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

table_html = "<table border='1' cellpadding='6' cellspacing='0'><thead><tr><th>subset</th><th>task_count</th><th>avg_n_train</th><th>avg_n_colors</th></tr></thead><tbody>"
for r in summary_rows:
    table_html += f"<tr><td>{r['subset']}</td><td>{r['task_count']}</td><td>{r['avg_n_train']}</td><td>{r['avg_n_colors']}</td></tr>"
table_html += "</tbody></table>"

html = f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>ARC Cleaning Report</title>
<style>body{{font-family:sans-serif;margin:24px;}} h1,h2{{color:#333;}}</style>
</head>
<body>
<h1>ARC 任务清洗报告</h1>
<p>本报告展示 abstraction-and-reasoning-challenge 数据集清洗后的 subset_summary 聚合结果。</p>
<h2>subset_summary</h2>
{table_html}
<h2>各 subset 任务数量</h2>
<img alt="task count chart" src="data:image/png;base64,{png_b64}" />
<h2>清洗概况</h2>
<ul>
<li>files_scanned: {files_scanned}</li>
<li>files_valid: {files_valid}</li>
<li>files_dropped: {files_dropped}</li>
</ul>
</body></html>
"""
with open(out_dir / "report.html", "w", encoding="utf-8") as f:
    f.write(html)

print("done", files_scanned, files_valid, files_dropped)
PY
