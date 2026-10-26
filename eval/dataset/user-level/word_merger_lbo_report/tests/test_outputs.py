#!/usr/bin/env python3
"""Three-layer verifier: M&A merger & LBO transaction analysis report.

Layer 1 (weight 0.40): Programmatic checks (page size, fonts, tables, charts, structure)
Layer 2 (weight 0.30): LLM code judge (code quality via API)
Layer 3 (weight 0.30): VLM visual judge (document appearance via API)

Pass threshold: total_score >= 0.70 → reward = 1
"""
import json
import os
import re
import time
from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

# [iq0822 host-rescan fix] 允许 host 打分时用 OUTPUT_DIR 覆盖读死的 /workspace(sandbox 内路径)。
# 默认仍是 /workspace(sandbox 内不受影响);host 上 score_reps.py 设 OUTPUT_DIR=<sample>/output,
# 令产物/代码/reward 全落在同一目录,与 score_reps 读取位置一致(避免 no reward.txt / Output file does not exist)。
_OUT_DIR = Path(os.getenv("OUTPUT_DIR", "/workspace"))
OUTPUT_PATH = _OUT_DIR / "report.docx"
CODE_PATH = _OUT_DIR / "generated_code.py"
CODE_PATH_ALT = _OUT_DIR / "solve.py"
REWARD_PATH = _OUT_DIR / "reward.txt" if os.getenv("OUTPUT_DIR") else Path("/logs/verifier/reward.txt")
COMPONENTS_PATH = _OUT_DIR / "reward_components.json" if os.getenv("OUTPUT_DIR") else Path("/logs/verifier/reward_components.json")
TESTS_DIR = Path(__file__).resolve().parent

# Three-layer weights
W_L1 = 0.40
W_L2 = 0.30
W_L3 = 0.30
PASS_THRESHOLD = 0.70
# [threshold-cut 默认口径] 用户拍板:total>=阈值直接门控成 reward=1.0;
# 低于阈值仍按原连续分 reward=total。阈值 0.85。这是评测默认行为,不再用 ORACLE_MODE 门控。
# 只加分支,不改 rubric/权重/评测口径。
ORACLE_THRESHOLD = float(os.environ.get("ORACLE_THRESHOLD", "0.85"))

# API config — NO hardcoded keys; must be injected via environment
API_BASE = os.environ.get("JUDGE_API_BASE", "") or os.environ.get("LLM_API_BASE", "")
API_KEY = os.environ.get("JUDGE_API_KEY", "") or os.environ.get("LLM_API_KEY", "")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-sonnet-4-6")
# Raise from the naive 1024 default: reasoning-mode judges (thinking models) burn
# their token budget on internal thinking tokens before emitting any content, and
# a 1024 cap leaves 0 tokens for the actual JSON output → all 3 retries fail silently.
JUDGE_MAX_TOKENS = int(os.environ.get("JUDGE_MAX_TOKENS", "8192"))

# ═══════════════════════════════════════════════════════════════════════════════
# Task-specific constants
# ═══════════════════════════════════════════════════════════════════════════════

PAGE_WIDTH_CM = 21.0
PAGE_HEIGHT_CM = 29.7
MARGIN_TOP_CM = 2.54
MARGIN_BOTTOM_CM = 2.54
MARGIN_LEFT_CM = 3.0
MARGIN_RIGHT_CM = 3.0
BODY_FONT_NAME_EXPECTED = "FangSong"
BODY_FONT_SIZE_PT = 12
HEADING_SPECS = {
    1: {"size_pt": 16, "bold": True, "alignment": "center"},
    2: {"size_pt": 14, "bold": True, "alignment": "left"},
    3: {"size_pt": 12, "bold": True, "alignment": "left"},
}
KNOWN_SECTION_HEADINGS = [
    "Deal Overview and Transaction Summary",
    "Purchase Price and Goodwill Allocation",
    "Combined Income Statement and Accretion",
    "WACC and DCF Valuation",
    "LBO Alternative Structure and Returns",
    "Sensitivity Analysis and Risk Factors",
]
MIN_TABLES = 3
MIN_TABLE_ROWS = 4
MIN_TABLE_COLS = 2

# ═══════════════════════════════════════════════════════════════════════════════
# L1: Programmatic checks
# ═══════════════════════════════════════════════════════════════════════════════

L1_WEIGHTS = {
    "v_docx_validity": 0.05,
    "v_page_dimensions": 0.03,
    "v_page_margins": 0.03,
    "v_body_font": 0.04,
    "v_heading_formatting": 0.05,
    "v_paragraph_formatting": 0.05,
    "v_table_present": 0.05,
    "v_chart_embedded": 0.05,
    "v_content_structure": 0.05,
}

_l1_results = {}
ALIGN_MAP = {"center": 1, "left": 0, "right": 2, "justify": 3}


def _record(cid, score):
    _l1_results[cid] = {"weight": L1_WEIGHTS.get(cid, 0.0), "score_value": min(max(score, 0.0), 1.0)}


def _normalize_font(name):
    return name.replace(" ", "").lower() if name else ""


def _pt_val(length):
    if length is None:
        return None
    try:
        return float(length.pt)
    except (TypeError, ValueError, AttributeError):
        return None


_HEADING_STYLE_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)


def _style_heading_level(para):
    try:
        name = para.style.name or ""
    except Exception:
        return None
    m = _HEADING_STYLE_RE.match(name.strip())
    if m:
        lvl = int(m.group(1))
        if lvl in HEADING_SPECS:
            return lvl
    return None


def _effective_font_attr(para, run, attr):
    """Resolve direct formatting, character style, then paragraph style.

    False and a wrong explicit size are real overrides, not missing values.
    """
    value = getattr(run.font, attr)
    if value is not None:
        return value
    for style in (run.style, para.style):
        seen = set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            value = getattr(style.font, attr)
            if value is not None:
                return value
            style = style.base_style
    return None


def _detect_heading_level(para):
    # A Heading 1 with wrong direct formatting is still a level-1 heading.
    # Inferring its level from the incorrect font would hide that error.
    level = _style_heading_level(para)
    if level is not None:
        return level
    for run in para.runs:
        if run.bold and run.font.size:
            size = _pt_val(run.font.size)
            if size and size > BODY_FONT_SIZE_PT:
                for level, spec in HEADING_SPECS.items():
                    if abs(size - spec['size_pt']) <= 1.0:
                        return level
    return None


def _style_font_size(para):
    try:
        return _pt_val(para.style.font.size)
    except Exception:
        return None


def _style_bold(para):
    try:
        return bool(para.style.font.bold)
    except Exception:
        return False


def _is_body_paragraph(para):
    if not para.text.strip():
        return False
    return _detect_heading_level(para) is None


def _open_doc():
    if not OUTPUT_PATH.exists() or OUTPUT_PATH.stat().st_size == 0:
        return None
    try:
        import docx
        return docx.Document(str(OUTPUT_PATH))
    except Exception:
        return None


class TestDocxValidity:
    def test_valid_docx(self):
        ok, msg = True, ""
        if not OUTPUT_PATH.exists():
            ok, msg = False, "Output file does not exist"
        elif OUTPUT_PATH.stat().st_size == 0:
            ok, msg = False, "Output file is 0 bytes"
        else:
            try:
                import docx
                docx.Document(str(OUTPUT_PATH))
            except Exception as e:
                ok, msg = False, f"Cannot open: {e}"
        _record("v_docx_validity", 1.0 if ok else 0.0)
        assert ok, msg


def test_page_dimensions():
    doc = _open_doc()
    if not doc:
        _record("v_page_dimensions", 0.0); pytest.skip("N/A")
    s = doc.sections[0]
    w_ok = abs(float(s.page_width.cm) - PAGE_WIDTH_CM) <= 0.1
    h_ok = abs(float(s.page_height.cm) - PAGE_HEIGHT_CM) <= 0.1
    _record("v_page_dimensions", (int(w_ok) + int(h_ok)) / 2.0)
    assert w_ok and h_ok


def test_page_margins():
    doc = _open_doc()
    if not doc:
        _record("v_page_margins", 0.0); pytest.skip("N/A")
    s = doc.sections[0]
    checks = [
        abs(float(s.top_margin.cm) - MARGIN_TOP_CM) <= 0.1,
        abs(float(s.bottom_margin.cm) - MARGIN_BOTTOM_CM) <= 0.1,
        abs(float(s.left_margin.cm) - MARGIN_LEFT_CM) <= 0.1,
        abs(float(s.right_margin.cm) - MARGIN_RIGHT_CM) <= 0.1,
    ]
    _record("v_page_margins", sum(checks) / 4.0)
    assert sum(checks) >= 3


def test_body_font():
    doc = _open_doc()
    if not doc:
        _record("v_body_font", 0.0); pytest.skip("N/A")
    style = doc.styles["Normal"]
    name_ok = _normalize_font(BODY_FONT_NAME_EXPECTED) in _normalize_font(style.font.name)
    sz = _pt_val(style.font.size)
    size_ok = sz is not None and abs(sz - BODY_FONT_SIZE_PT) <= 1.0
    _record("v_body_font", (int(name_ok) + int(size_ok)) / 2.0)
    assert name_ok and size_ok


def test_heading_formatting():
    doc = _open_doc()
    if not doc:
        _record('v_heading_formatting', 0.0)
        pytest.skip('N/A')
    total = passed = 0
    for para in doc.paragraphs:
        level = _detect_heading_level(para)
        if level not in HEADING_SPECS:
            continue
        spec = HEADING_SPECS[level]
        runs = [run for run in para.runs if run.text.strip()]
        sizes = [_pt_val(_effective_font_attr(para, run, 'size')) for run in runs]
        size_ok = bool(runs) and all(
            size is not None and abs(size - spec['size_pt']) <= 1.0 for size in sizes
        )
        bold_ok = bool(runs) and all(
            _effective_font_attr(para, run, 'bold') is True for run in runs
        )
        align_ok = (para.alignment == ALIGN_MAP.get(spec['alignment'], -1)
                    if para.alignment is not None else True)
        total += 3
        passed += int(size_ok) + int(bold_ok) + int(align_ok)
    score = passed / total if total else 0.0
    _record('v_heading_formatting', score)
    assert total > 0 and score >= 0.3


def test_paragraph_formatting():
    doc = _open_doc()
    if not doc:
        _record("v_paragraph_formatting", 0.0); pytest.skip("N/A")
    body = [p for p in doc.paragraphs if _is_body_paragraph(p)]
    sp_ok = sp_tot = ind_ok = ind_tot = 0
    for para in body:
        pf = para.paragraph_format
        if pf and pf.line_spacing is not None:
            sp_tot += 1
            try:
                if 1.4 <= float(pf.line_spacing) <= 1.6: sp_ok += 1
            except (TypeError, ValueError): pass
        if pf and pf.first_line_indent is not None:
            ind_tot += 1
            try:
                if 21 <= float(pf.first_line_indent.pt) <= 27: ind_ok += 1
            except (TypeError, ValueError, AttributeError): pass
    sr = sp_ok / sp_tot if sp_tot else 0.0
    ir = ind_ok / ind_tot if ind_tot else 0.0
    _record("v_paragraph_formatting", (sr + ir) / 2.0)
    assert (sr + ir) / 2.0 >= 0.3


def test_table_present():
    doc = _open_doc()
    if not doc:
        _record("v_table_present", 0.0); pytest.skip("N/A")
    n = len(doc.tables)
    tables_ok = n >= MIN_TABLES
    rows_ok = cols_ok = bold_ok = False
    for tbl in doc.tables:
        if len(tbl.rows) >= MIN_TABLE_ROWS: rows_ok = True
        if len(tbl.columns) >= MIN_TABLE_COLS: cols_ok = True
        if tbl.rows:
            for cell in tbl.rows[0].cells:
                for p in cell.paragraphs:
                    for r in p.runs:
                        if r.bold: bold_ok = True
    score = (int(tables_ok) + int(rows_ok) + int(cols_ok) + int(bold_ok)) / 4.0
    _record("v_table_present", score)
    assert tables_ok


def test_chart_embedded():
    doc = _open_doc()
    if not doc:
        _record("v_chart_embedded", 0.0); pytest.skip("N/A")
    ok = len(doc.inline_shapes) >= 1
    _record("v_chart_embedded", 1.0 if ok else 0.0)
    assert ok


def test_content_structure():
    doc = _open_doc()
    if not doc:
        _record("v_content_structure", 0.0); pytest.skip("N/A")
    text = " ".join(p.text for p in doc.paragraphs)
    found = sum(1 for h in KNOWN_SECTION_HEADINGS if h in text)
    _record("v_content_structure", found / len(KNOWN_SECTION_HEADINGS))
    assert found >= 3


# ═══════════════════════════════════════════════════════════════════════════════
# L2: LLM Code Judge
# ═══════════════════════════════════════════════════════════════════════════════

LLM_DIMENSIONS = [
    "code_data_extraction",
    "code_data_presentation",
    "code_chart_logic",
    "code_completeness",
    "code_correctness",
]

LLM_SYSTEM_PROMPT = """You are a code reviewer evaluating a Python script that generates a Word document (.docx) from Excel data.

You will be given:
1. The TASK INSTRUCTION describing what the script should produce
2. EXPECTED DATA from the Excel file (key values and table structures the script should extract)
3. The GENERATED CODE to evaluate

Score each dimension from 0.0 to 1.0. Be fair — a working solution that addresses the requirements should score 0.8+. Only deduct significantly for clear errors or missing functionality.

Scoring guide:
- 0.9-1.0: Excellent — fully correct, handles edge cases
- 0.7-0.9: Good — mostly correct with minor issues
- 0.5-0.7: Partial — significant gaps but shows understanding
- 0.0-0.5: Poor — major errors or missing entirely

Dimensions:
1. code_data_extraction (0-1): Does the code open the correct Excel file and read from appropriate sheets/cells? Give 0.9+ if it reads from the right sheets even if cell references differ slightly.
2. code_data_presentation (0-1): Are values presented in tables with formatting (numbers, percentages)? Give 0.9+ if tables are created with data, even if formatting differs from expected.
3. code_chart_logic (0-1): Is a matplotlib chart created with relevant data? Give 0.9+ if any appropriate chart is made from the data.
4. code_completeness (0-1): Does the code produce a multi-section report with heading hierarchy? Give 0.9+ if it has all major sections.
5. code_correctness (0-1): Will the code likely run without errors? Uses python-docx/openpyxl APIs correctly? Give 0.9+ if no obvious runtime errors.

OUTPUT FORMAT (JSON only, no markdown fences):
{
  "code_data_extraction": 0.8,
  "code_data_presentation": 0.9,
  "code_chart_logic": 0.7,
  "code_completeness": 1.0,
  "code_correctness": 0.9,
  "reasoning": "Brief explanation of scores"
}"""


def _find_code_file():
    """Find generated Python code file — search broadly since models pick their own filename."""
    # Priority 1: canonical paths
    for p in [CODE_PATH, CODE_PATH_ALT]:
        if p.exists() and p.stat().st_size > 0:
            return p

    # Priority 2: any .py in /workspace/ (models often name it generate_report.py, main.py, etc.)
    root = Path("/root")
    if root.exists():
        candidates = []
        for p in root.glob("*.py"):
            n = p.name
            if n.startswith(("test_", ".", "_")) or n in ("conftest.py", "setup.py", "__init__.py"):
                continue
            if p.stat().st_size > 0:
                candidates.append(p)
        if candidates:
            # Prefer the largest — the main solution script rather than a helper snippet
            return max(candidates, key=lambda p: p.stat().st_size)

    # Priority 3: sibling solution/ dir (local golden-testing convenience)
    solution_dir = TESTS_DIR.parent / "solution"
    if solution_dir.exists():
        py_files = [p for p in solution_dir.glob("*.py") if p.stat().st_size > 0]
        if py_files:
            return max(py_files, key=lambda p: p.stat().st_size)

    return None


def _run_llm_judge():
    if not API_BASE or not API_KEY:
        return 0.0, {}

    code_file = _find_code_file()
    if not code_file:
        return 0.0, {}

    try:
        import httpx
    except ImportError:
        return 0.0, {}

    instruction_path = TESTS_DIR.parent / "instruction.md"
    expected_data_path = TESTS_DIR / "expected_data.json"

    instruction = instruction_path.read_text()[:4000] if instruction_path.exists() else ""
    expected_data = ""
    if expected_data_path.exists():
        expected_data = json.dumps(json.loads(expected_data_path.read_text()), indent=2, ensure_ascii=False)[:6000]
    # [2026-09-19 fix] 原截断 16000 字符:本题交付代码常 ≥59KB,截断点落在 helper
    # 函数内部,所有 section/图表/main 均在窗口外 ⇒ LLM judge 把完整可运行代码误判
    # completeness/correctness=0(实测 L2=0.13,应约 0.9)。放宽到 60000(与同组
    # word_lbo_returns_report 口径一致),只改截断上限,不改判分标准。
    code = code_file.read_text()[:60000]

    user_prompt = f"""## TASK INSTRUCTION
{instruction}

## EXPECTED DATA (from Excel)
{expected_data}

## GENERATED CODE
```python
{code}
```

Score the code on all 5 dimensions. Output JSON only."""

    messages = [
        {"role": "system", "content": LLM_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(3):
        try:
            resp = httpx.post(
                f"{API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": JUDGE_MODEL, "messages": messages, "temperature": 0.1, "max_tokens": JUDGE_MAX_TOKENS},
                timeout=120,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            scores = json.loads(content)
            if all(d in scores for d in LLM_DIMENSIONS):
                result = {d: max(0.0, min(1.0, float(scores[d]))) for d in LLM_DIMENSIONS}
                avg = sum(result.values()) / len(result)
                return avg, result
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
    return 0.0, {}


# ═══════════════════════════════════════════════════════════════════════════════
# L3: VLM Visual Judge
# ═══════════════════════════════════════════════════════════════════════════════

VLM_DIMENSIONS = [
    "visual_layout",
    "visual_tables",
    "visual_chart",
    "visual_typography",
    "visual_overall",
]

VLM_SYSTEM_PROMPT = """You are a document quality judge evaluating professional Word reports based on their HTML structure.

The document was converted from .docx to HTML. Based on the HTML structure, infer the visual quality:
- Are headings properly hierarchical (h1 > h2 > h3)?
- Are tables well-structured with proper headers?
- Is content organized in clear sections?
- Does it appear professional (paragraphs, lists, tables present)?

Score each dimension from 0.0 to 1.0:

1. visual_layout (0-1): Content well-organized, sections clearly delineated, no apparent overflow or missing structure
2. visual_tables (0-1): Tables have proper rows/columns/headers, data is formatted consistently
3. visual_chart (0-1): An image/chart appears to be embedded (img tag present), or data that would form a chart
4. visual_typography (0-1): Heading hierarchy present (strong/bold for headers), consistent structure
5. visual_overall (0-1): Overall professional quality — well-structured report with all expected components

OUTPUT FORMAT (JSON only, no markdown fences):
{
  "visual_layout": 0.8,
  "visual_tables": 0.9,
  "visual_chart": 0.7,
  "visual_typography": 0.8,
  "visual_overall": 0.8,
  "reasoning": "Brief explanation"
}"""


def _run_vlm_judge():
    if not API_BASE or not API_KEY:
        return 0.0, {}

    if not OUTPUT_PATH.exists() or OUTPUT_PATH.stat().st_size == 0:
        return 0.0, {}

    try:
        import httpx
        import mammoth
    except ImportError:
        return 0.0, {}

    try:
        with open(OUTPUT_PATH, "rb") as f:
            result = mammoth.convert_to_html(f)
            html = result.value
    except Exception:
        return 0.0, {}

    if not html:
        return 0.0, {}

    messages = [
        {"role": "system", "content": VLM_SYSTEM_PROMPT},
        {"role": "user", "content": f"Evaluate this document's quality based on its HTML structure:\n\n{html[:12000]}"},
    ]

    for attempt in range(3):
        try:
            resp = httpx.post(
                f"{API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": JUDGE_MODEL, "messages": messages, "temperature": 0.1, "max_tokens": JUDGE_MAX_TOKENS},
                timeout=120,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            scores = json.loads(content)
            if all(d in scores for d in VLM_DIMENSIONS):
                result = {d: max(0.0, min(1.0, float(scores[d]))) for d in VLM_DIMENSIONS}
                avg = sum(result.values()) / len(result)
                return avg, result
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
    return 0.0, {}


# ═══════════════════════════════════════════════════════════════════════════════
# Final reward computation
# ═══════════════════════════════════════════════════════════════════════════════

def _write_reward():
    os.makedirs("/logs/verifier", exist_ok=True)

    # L1: programmatic score (sum of weighted components, max = 0.40)
    l1_raw = round(sum(r["weight"] * r["score_value"] for r in _l1_results.values()), 4) if _l1_results else 0.0
    l1_normalized = min(l1_raw / 0.40, 1.0) if 0.40 > 0 else 0.0

    # L2: LLM code judge
    l2_score, l2_components = _run_llm_judge()

    # L3: VLM visual judge
    l3_score, l3_components = _run_vlm_judge()

    # Combined
    total = round(W_L1 * l1_normalized + W_L2 * l2_score + W_L3 * l3_score, 4)
    # [iq0822 fix] 原为 reward = 1.0 if total >= 0.70 else 0.0(二值,无部分分)。
    # 阈值卡在 0.70 时,只要任一 judge 静默失败,total 上限 0.4*L1+0.3*L3≈0.66 ⇒ 结构性不可通过。
    # 改为连续分,与 clibench 判分链口径一致;pass_threshold 仅作诊断保留。
    # [oracle threshold] oracle 校准模式:total>=ORACLE_THRESHOLD 门控为满分(主观 judge 够不到严格 1.0)。
    # [threshold-cut 默认口径] total>=阈值门控为满分;低于阈值仍按连续分 reward=total。
    reward = 1.0 if total >= ORACLE_THRESHOLD else total

    REWARD_PATH.write_text(str(reward))
    COMPONENTS_PATH.write_text(json.dumps({
        "total_score": total,
        "pass": total >= PASS_THRESHOLD,
        "reward": reward,
        "pass_threshold": PASS_THRESHOLD,
        "l1_programmatic": {"score": l1_normalized, "weight": W_L1, "raw": l1_raw, "components": _l1_results},
        "l2_llm_judge": {"score": l2_score, "weight": W_L2, "components": l2_components},
        "l3_vlm_judge": {"score": l3_score, "weight": W_L3, "components": l3_components},
    }, indent=2, ensure_ascii=False))


@pytest.fixture(scope="session", autouse=True)
def _write_reward_at_end(request):
    yield
    _write_reward()
