#!/bin/bash
set -e
cd /app

python3 - <<'PY'
import json
import os

DRAFT_PATH = "/app/input/desc_state.draft.json"
OUT_PATH = "/app/output/desc_state.repaired.json"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

FORBIDDEN = ["...", "…", "省略", "etc.", "and so on", "TODO", "TBD"]

# canonical correction targets — must match build_data.py
DEFERRED_ITEM = "员工手册英文版暂缓，待中文版七章定稿后再启动翻译。"
OPEN_QUESTION_ITEM = "Notion 的 Business 套餐是否覆盖所需的 AI 集成额度，需要向管理员确认。"

with open(DRAFT_PATH, encoding="utf-8") as f:
    d = json.load(f)


def has_placeholder(s):
    return any(p in s for p in FORBIDDEN)


# --- task_overview ---------------------------------------------------------
to = d["task_overview"]
to["final_status"] = "unknown"  # evidence: final status unknown at iteration-1
to["task_threads"] = [t for t in to["task_threads"] if not has_placeholder(t)]

# --- user_intent -----------------------------------------------------------
ui = d["user_intent"]
# fix the TODO requirement -> complete content
fixed_reqs = []
for r in ui["current_requirements"]:
    if "TODO" in r:
        fixed_reqs.append("补充客户结构统计页（中老年50%/青壮年30%/青少年20%）")
    else:
        fixed_reqs.append(r)
ui["current_requirements"] = fixed_reqs
# route misfiled items out of ambiguities
amb = ui["ambiguities"]
ui["deferred_followups"] = list(ui.get("deferred_followups", [])) + [a for a in amb if a == DEFERRED_ITEM]
ui["open_questions"] = list(ui.get("open_questions", [])) + [a for a in amb if a == OPEN_QUESTION_ITEM]
ui["ambiguities"] = [a for a in amb if a not in (DEFERRED_ITEM, OPEN_QUESTION_ITEM)]
# scrub the persona hint placeholder
ui["user_persona_hint"] = "健康养生行业的中小企业管理者，关注合规、品牌建设与制度落地。"

# --- workspace -------------------------------------------------------------
ws = d["workspace"]
# evidence: platform is Notion, no Feishu app exists -> drop Feishu service
ws["services_or_modules"] = [
    s for s in ws["services_or_modules"]
    if "Feishu" not in s["name"] and "飞书" not in s["name"]
]
# drop placeholder constraints
ws["environment_constraints"] = [c for c in ws["environment_constraints"] if not has_placeholder(c)]

# --- reward ----------------------------------------------------------------
d["reward"]["reference_solution_path"] = "unknown"  # no reference solution at iteration-1

# --- final safety scrub: no placeholder may remain anywhere ----------------
def scrub(x):
    if isinstance(x, str):
        return "unknown" if has_placeholder(x) else x
    if isinstance(x, list):
        return [scrub(i) for i in x]
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items()}
    return x

d = scrub(d)

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)

print(f"[solve] wrote repaired desc_state to {OUT_PATH}")
PY
