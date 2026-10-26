#!/usr/bin/env python3
"""Golden solver: reads Merger & LBO input.xlsx and generates M&A transaction analysis report."""
import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openpyxl import load_workbook
from docx import Document
from docx.shared import Cm, Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

INPUT_PATH = Path("/workspace/data/input.xlsx")
OUTPUT_PATH = Path("/workspace/report.docx")


# [oracle] 把本脚本源码复制到 /workspace/generated_code.py,供 L2 code judge 读取
# (判分器 _find_code_file 优先读该路径;solve.py 本身不在 /workspace,不复制则 L2 找不到代码 → L2=0)
def _emit_generated_code():
    import shutil, os
    try:
        os.makedirs('/workspace', exist_ok=True)
        shutil.copyfile(os.path.abspath(__file__), '/workspace/generated_code.py')
    except Exception as e:
        print(f'warn: could not emit generated_code.py: {e}')


def add_page_number(footer):
    para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run()
    fldChar1 = OxmlElement('w:fldChar')
    fldChar1.set(qn('w:fldCharType'), 'begin')
    instrText = OxmlElement('w:instrText')
    instrText.set(qn('xml:space'), 'preserve')
    instrText.text = ' PAGE '
    fldChar2 = OxmlElement('w:fldChar')
    fldChar2.set(qn('w:fldCharType'), 'end')
    run._element.append(fldChar1)
    run._element.append(instrText)
    run._element.append(fldChar2)


def add_heading_formatted(doc, text, level):
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.bold = True
    if level == 1:
        run.font.size = Pt(16)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif level == 2:
        run.font.size = Pt(14)
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    elif level == 3:
        run.font.size = Pt(12)
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run.font.name = "FangSong"
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:eastAsia'), 'FangSong')
    rPr.append(rFonts)


def add_body_paragraph(doc, text):
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.font.size = Pt(12)
    run.font.name = "FangSong"
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:eastAsia'), 'FangSong')
    rPr.append(rFonts)
    pf = para.paragraph_format
    pf.line_spacing = 1.5
    pf.first_line_indent = Pt(24)


def set_cell_border(cell):
    tcPr = cell._element.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    for edge in ('top', 'left', 'bottom', 'right'):
        border = OxmlElement(f'w:{edge}')
        border.set(qn('w:val'), 'single')
        border.set(qn('w:sz'), '4')
        border.set(qn('w:color'), '000000')
        tcBorders.append(border)
    tcPr.append(tcBorders)


def add_table_with_data(doc, headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"

    # Header row
    hdr = table.rows[0]
    for i, h in enumerate(headers):
        cell = hdr.cells[i]
        cell.text = ""
        para = cell.paragraphs[0]
        run = para.add_run(str(h))
        run.bold = True
        run.font.size = Pt(11)
        run.font.name = "FangSong"
        rPr = run._element.get_or_add_rPr()
        rFonts = OxmlElement('w:rFonts')
        rFonts.set(qn('w:eastAsia'), 'FangSong')
        rPr.append(rFonts)
        set_cell_border(cell)

    # Data rows
    for i, row in enumerate(rows):
        tr = table.rows[i + 1]
        for j, val in enumerate(row):
            if j >= len(tr.cells):
                break
            cell = tr.cells[j]
            cell.text = ""
            para = cell.paragraphs[0]
            run = para.add_run(str(val))
            run.font.size = Pt(11)
            run.font.name = "FangSong"
            rPr = run._element.get_or_add_rPr()
            rFonts = OxmlElement('w:rFonts')
            rFonts.set(qn('w:eastAsia'), 'FangSong')
            rPr.append(rFonts)
            set_cell_border(cell)
    return table


def fmt_money(v, digits=1):
    if v is None or (isinstance(v, str) and v.strip() == "-"):
        return "-"
    try:
        return f"${float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def fmt_num(v, digits=1):
    if v is None or (isinstance(v, str) and v.strip() == "-"):
        return "-"
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v, digits=1):
    if v is None:
        return "-"
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(v)


def fmt_x(v, digits=1):
    if v is None:
        return "-"
    try:
        return f"{float(v):.{digits}f}x"
    except (TypeError, ValueError):
        return str(v)


def main():
    wb = load_workbook(str(INPUT_PATH), data_only=True)
    mm = wb["Merger-Model"]
    lbo = wb["Basic-LBO"]
    dcf = wb["DCF"]
    wacc = wb["WACC"]

    # ----- Extract Merger-Model data -----
    per_share_price = mm.cell(7, 6).value
    pct_cash = mm.cell(8, 6).value
    pct_debt = mm.cell(9, 6).value
    pct_stock = mm.cell(10, 6).value
    equity_purchase_price = mm.cell(19, 6).value
    total_allocable = mm.cell(22, 6).value
    total_goodwill = mm.cell(28, 6).value

    # Purchase price allocation (rows 19-28)
    ppa_rows = []
    for r, label in [(19, "Equity Purchase Price"), (20, "Less: Seller Book Value"),
                      (21, "Plus: Write-Off of Deferred Tax"), (22, "Total Allocable Purchase Premium"),
                      (24, "Less: Write-Up of PP&E"), (25, "Less: Write-Up of Intangibles"),
                      (26, "Less: Write-Down of Deferred Tax"), (27, "Plus: New Deferred Tax Liability"),
                      (28, "Total Goodwill Created")]:
        v = mm.cell(r, 6).value
        ppa_rows.append([label, fmt_money(v)])

    # Combined income statement FY2010E, FY2011E (rows 66-90)
    ci_rows = []
    for r, label in [(66, "Combined Revenue"), (67, "Revenue Synergies"),
                      (70, "Gross Profit"), (81, "Operating Income"),
                      (88, "Net Income"), (90, "EPS")]:
        e = mm.cell(r, 5).value
        f = mm.cell(r, 6).value
        if label == "EPS":
            ci_rows.append([label, f"${fmt_num(e, 2)}", f"${fmt_num(f, 2)}"])
        else:
            ci_rows.append([label, fmt_money(e), fmt_money(f)])

    ad_2010 = mm.cell(93, 5).value
    ad_2011 = mm.cell(93, 6).value
    ad_pct_2010 = mm.cell(94, 5).value
    ad_pct_2011 = mm.cell(94, 6).value

    # ----- LBO data -----
    lbo_share_price = lbo.cell(7, 6).value  # 192.06
    lbo_premium = lbo.cell(8, 6).value  # 0.2
    lbo_offer_price = lbo.cell(9, 6).value  # 230.472
    lbo_pct_debt = lbo.cell(10, 6).value  # 0.5
    lbo_pct_equity = lbo.cell(11, 6).value  # 0.5
    lbo_ev = lbo.cell(23, 6).value  # 177318

    # Sources & Uses (rows 37-40, col F for sources, col K for uses)
    bank_debt = lbo.cell(37, 6).value
    high_yield = lbo.cell(38, 6).value
    investor_equity = lbo.cell(39, 6).value
    total_sources = lbo.cell(40, 6).value

    uses_equity = lbo.cell(37, 12).value  # col L
    uses_advisory = lbo.cell(38, 12).value
    uses_financing = lbo.cell(39, 12).value
    total_uses = lbo.cell(40, 12).value

    # IRR from row 138, col H (8) — the computed values live in col H, not G
    lbo_irr = lbo.cell(138, 8).value

    # EBITDA multiples row 133-134 (col H)
    lbo_ebitda = lbo.cell(133, 8).value
    lbo_ebitda_mult = lbo.cell(134, 8).value

    # ----- DCF data -----
    dcf_years_labels = []
    for c in range(6, 10):
        v = dcf.cell(6, c).value
        if v:
            dcf_years_labels.append(str(v))
        else:
            dcf_years_labels.append(f"FY {2010+c-6}E")

    dcf_rows = []
    for r, label in [(8, "Revenue"), (9, "EBITDA"), (10, "Operating Income"),
                      (21, "Unlevered Free Cash Flow"), (22, "Present Value of FCF")]:
        row = [label] + [fmt_money(dcf.cell(r, c).value) for c in range(6, 10)]
        dcf_rows.append(row)

    # ----- WACC data -----
    risk_free = wacc.cell(6, 5).value  # 0.0438
    erp = wacc.cell(7, 5).value  # 0.07
    interest_rate = wacc.cell(8, 5).value  # 0.09
    median_unlevered = wacc.cell(19, 7).value  # 1.159
    apple_levered = wacc.cell(27, 7).value  # 1.159
    cost_of_equity = wacc.cell(29, 7).value  # 0.125
    final_wacc = wacc.cell(34, 7).value  # 0.125

    wacc_rows = [
        ["Risk-Free Rate", fmt_pct(risk_free)],
        ["Equity Risk Premium", fmt_pct(erp)],
        ["Interest Rate on Debt", fmt_pct(interest_rate)],
        ["Median Unlevered Beta (Comps)", fmt_num(median_unlevered, 2)],
        ["Apple Levered Beta", fmt_num(apple_levered, 2)],
        ["Cost of Equity", fmt_pct(cost_of_equity)],
        ["WACC", fmt_pct(final_wacc)],
    ]

    # ===== Build the document =====
    doc = Document()

    # Page setup
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(3.0)

    # Normal style: FangSong 12pt
    normal = doc.styles["Normal"]
    normal.font.name = "FangSong"
    normal.font.size = Pt(12)
    rPr = normal.element.get_or_add_rPr()
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:eastAsia'), 'FangSong')
    rPr.append(rFonts)

    # Footer page number
    add_page_number(section.footer)

    # Title
    add_heading_formatted(doc, "Apple Inc. — M&A Transaction Analysis Report", 1)
    add_body_paragraph(doc, "Prepared by the Corporate Development Team for the Investment Committee and Board of Directors")

    # === Section 1: Deal Overview ===
    add_heading_formatted(doc, "1. Deal Overview and Transaction Summary", 2)
    add_body_paragraph(doc,
        "Apple Inc., a leading global technology company, is evaluating the strategic acquisition of a technology target "
        "to expand its portfolio and accelerate growth in adjacent markets. This report presents the transaction structure, "
        "valuation, projected combined performance, and an alternative leveraged buyout scenario for consideration by the "
        "Investment Committee and Board of Directors.")
    add_body_paragraph(doc,
        f"The proposed transaction contemplates a per-share purchase price of ${fmt_num(per_share_price, 2)} with a mixed "
        f"consideration structure of {fmt_pct(pct_cash)} Cash / {fmt_pct(pct_debt)} Debt / {fmt_pct(pct_stock)} Stock. "
        f"The total equity purchase price is {fmt_money(equity_purchase_price)}M, which will create "
        f"{fmt_money(total_goodwill)}M of goodwill after purchase price allocation adjustments for asset write-ups and "
        f"deferred tax items.")

    # === Section 2: Purchase Price Allocation ===
    add_heading_formatted(doc, "2. Purchase Price and Goodwill Allocation", 2)
    add_body_paragraph(doc,
        "The purchase price allocation methodology begins with the total equity purchase consideration and adjusts for "
        "the seller's existing book value, deferred tax write-offs, PP&E and intangible asset write-ups, and the creation "
        "of new deferred tax liabilities. The residual amount represents the goodwill created in the transaction.")
    add_table_with_data(doc, ["Item", "Amount ($M)"], ppa_rows)

    # === Section 3: Combined Income Statement ===
    add_heading_formatted(doc, "3. Combined Income Statement and Accretion / Dilution", 2)
    add_body_paragraph(doc,
        "The combined entity is projected to generate substantial revenue and earnings growth in the two years post-close. "
        "The table below summarizes the combined pro-forma income statement.")
    add_table_with_data(doc, ["Item", "FY 2010E ($M)", "FY 2011E ($M)"], ci_rows)
    add_body_paragraph(doc,
        f"Earnings accretion analysis shows the transaction is accretive to EPS by ${fmt_num(ad_2010, 2)} "
        f"({fmt_pct(ad_pct_2010)}) in FY 2010E and by ${fmt_num(ad_2011, 2)} ({fmt_pct(ad_pct_2011)}) in FY 2011E, "
        "supported by revenue synergies of ~10% and cost synergies of ~10% assumed in the model.")

    # === Section 4: WACC and DCF Valuation ===
    add_heading_formatted(doc, "4. WACC and DCF Valuation", 2)
    add_body_paragraph(doc,
        "The weighted average cost of capital was derived from comparable technology company betas (Hewlett-Packard, Dell, "
        "Cisco, Intel, Motorola) and Apple's target capital structure.")
    add_table_with_data(doc, ["Item", "Value"], wacc_rows)

    add_body_paragraph(doc,
        "The discounted cash flow valuation projects Apple's standalone unlevered free cash flow through the forecast period, "
        "applies mid-year discounting convention, and calculates present values using the derived WACC. Terminal value is "
        "estimated using both EBITDA exit multiples and perpetuity growth methods (sensitivity tables in appendix).")
    add_table_with_data(doc, ["Item"] + dcf_years_labels, dcf_rows)

    # === Section 5: LBO Alternative ===
    add_heading_formatted(doc, "5. LBO Alternative Structure and Returns", 2)
    add_body_paragraph(doc,
        f"As an alternative to the strategic merger, a leveraged buyout scenario was modeled with an offer premium of "
        f"{fmt_pct(lbo_premium)} over the current share price of ${fmt_num(lbo_share_price, 2)}, yielding an offer price "
        f"of ${fmt_num(lbo_offer_price, 2)} per share and an implied enterprise value of {fmt_money(lbo_ev)}M "
        f"(EBITDA {fmt_num(lbo_ebitda, 0)}M × {fmt_num(lbo_ebitda_mult, 1)}x). The transaction would be financed "
        f"{fmt_pct(lbo_pct_debt)} Debt / {fmt_pct(lbo_pct_equity)} Equity.")

    sources_uses = [
        ["Bank Debt", fmt_money(bank_debt), "Equity Value of Target", fmt_money(uses_equity)],
        ["High-Yield Debt", fmt_money(high_yield), "Advisory Fees", fmt_money(uses_advisory)],
        ["Investor Equity", fmt_money(investor_equity), "Financing Fees", fmt_money(uses_financing)],
        ["Total Sources", fmt_money(total_sources), "Total Uses", fmt_money(total_uses)],
    ]
    add_table_with_data(doc, ["Sources", "Amount ($M)", "Uses", "Amount ($M)"], sources_uses)

    add_body_paragraph(doc,
        f"Under this LBO structure, the projected investor internal rate of return (IRR) is {fmt_pct(lbo_irr)} "
        "based on the modeled exit assumptions. Sensitivity analyses on exit multiple and debt percentage are provided "
        "in the appendix.")

    # Chart: debt tranche composition
    fig, ax = plt.subplots(figsize=(7, 4.5))
    tranche_labels = ["Bank Debt", "High-Yield Debt", "Investor Equity"]
    tranche_values = [bank_debt or 0, high_yield or 0, investor_equity or 0]
    colors = ["#2E86AB", "#A23B72", "#F18F01"]
    ax.bar(tranche_labels, tranche_values, color=colors)
    ax.set_ylabel("Amount ($M)", fontsize=10)
    ax.set_title("LBO Financing — Sources of Capital", fontsize=12, fontweight="bold")
    for i, v in enumerate(tranche_values):
        ax.text(i, v * 1.01, f"${v:,.0f}M", ha="center", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    img_buf = io.BytesIO()
    fig.savefig(img_buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    img_buf.seek(0)
    doc.add_picture(img_buf, width=Inches(5.5))

    # === Section 6: Sensitivity and Risks ===
    add_heading_formatted(doc, "6. Sensitivity Analysis and Risk Factors", 2)
    add_body_paragraph(doc,
        "The DCF valuation was stress-tested by varying the discount rate (10-12%) and terminal EBITDA exit multiple (4x-9x). "
        "Similarly, the LBO returns were sensitized against purchase premium (0-45%), exit multiple, and debt percentage. "
        "Detailed sensitivity tables are maintained in the underlying Excel model.")
    add_body_paragraph(doc,
        "Key deal risks include: (1) Financing risk — availability and cost of Bank Debt and High-Yield Debt in current "
        "credit markets; (2) Integration risk — execution challenges in combining organizations, product portfolios, and "
        "operating systems; (3) Synergy realization risk — the modeled ~10% revenue synergies and ~10% cost synergies "
        "assume successful cross-selling and operational integration; (4) Market conditions — macroeconomic factors and "
        "competitive dynamics may impact standalone and combined performance; (5) Regulatory approvals — the transaction "
        "may be subject to antitrust and other regulatory review.")

    doc.save(str(OUTPUT_PATH))
    print(f"Report saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    _emit_generated_code()
    main()
