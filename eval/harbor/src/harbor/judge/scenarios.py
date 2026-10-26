"""v0.4 维度体系（domains.md + dim_selection.json）共享加载层。

移植自 clibench ``pipeline/domains.py``（harbor 不能 import clibench）。
与原版差异：路径不再钉死 CLIBENCH_ROOT，全部由调用方解析后传入
（runner 从 task/instance 目录向上逐级查找，env 可覆盖）。

约定：场景 ID = `子维度号.场景号`（如 2.2.1）；G 场景不需要探针，
每条轨迹天然可评，不进入剧本 probes。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

SCENARIO_ID_RE = re.compile(r"^\d+\.\d+\.\d+$")

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
_DIM_RE = re.compile(r"^## ([一二三四五六七八九十]+)、(.+)$")
_SUB_RE = re.compile(r"^### (\d+)\.(\d+) (.+?)（(\d+)）\s*$")
_ROW_RE = re.compile(r"^\| ([①-⑮]) \| (.*?) \| ?(.*?)\s*\|\s*$")


def find_upwards(start: Path, filename: str) -> Path | None:
    """从 start 向上逐级找名为 filename 的文件（instance 可能位于
    instances/<iid> 两层或 candidates/<batch>/<iid> 三层）。"""
    for parent in start.parents:
        cand = parent / filename
        if cand.is_file():
            return cand
    return None


def load_domains(domains_md: Path) -> list[dict[str, Any]]:
    """解析 domains.md 为维度树：
    [{id, name, subdims: [{id, name, count, scenarios: [{id, text, g}]}]}]。"""
    dims: list[dict[str, Any]] = []
    dim = None
    sub = None
    for raw in domains_md.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m = _DIM_RE.match(line)
        if m:
            dim = {"id": m.group(1), "name": m.group(2).strip(), "subdims": []}
            dims.append(dim)
            sub = None
            continue
        m = _SUB_RE.match(line)
        if m and dim is not None:
            sub = {
                "id": f"{m.group(1)}.{m.group(2)}",
                "name": m.group(3).strip(),
                "count": int(m.group(4)),
                "scenarios": [],
            }
            subdims = cast(list[dict[str, Any]], dim["subdims"])
            subdims.append(sub)
            continue
        m = _ROW_RE.match(line)
        if m and sub is not None:
            idx = CIRCLED.index(m.group(1)) + 1
            scenarios = cast(list[dict[str, Any]], sub["scenarios"])
            scenarios.append(
                {
                    "id": f"{sub['id']}.{idx}",
                    "text": m.group(2).strip(),
                    "g": "G" in m.group(3),
                }
            )
    return dims


def load_selection(selection_json: Path | None) -> dict[str, Any]:
    """读场景 ID 选择集合。兼容两种格式：冻结快照树（dims）与页面实时
    选择文件（selected）。缺失时返回空集合（无 G 场景追加）。"""
    if selection_json is None or not selection_json.is_file():
        return {"dimensions": [], "subdims": [], "scenarios": []}
    obj = json.loads(selection_json.read_text(encoding="utf-8"))
    if "selected" in obj:
        return obj.get("selected") or {
            "dimensions": [],
            "subdims": [],
            "scenarios": [],
        }
    dims, subdims, scenarios = [], [], []
    for d in obj.get("dims", []):
        dims.append(d["id"])
        for sd in d.get("subdims", []):
            subdims.append(sd["id"])
            scenarios.extend(s["id"] for s in sd.get("scenarios", []))
    return {"dimensions": dims, "subdims": subdims, "scenarios": scenarios}


def scenario_index(dims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{scenario_id: {id, text, g, subdim, subdim_name, dim, dim_name}}"""
    out: dict[str, dict[str, Any]] = {}
    for d in dims:
        for sd in d["subdims"]:
            for s in sd["scenarios"]:
                out[s["id"]] = {
                    **s,
                    "subdim": sd["id"],
                    "subdim_name": sd["name"],
                    "dim": d["id"],
                    "dim_name": d["name"],
                }
    return out
