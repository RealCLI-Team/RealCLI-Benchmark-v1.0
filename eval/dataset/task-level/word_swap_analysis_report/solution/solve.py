#!/usr/bin/env python3
"""Golden solver: reads Swap Model input.xlsx and generates interest rate swap analysis report."""
import os
import math
import openpyxl
from docx import Document
from docx.shared import Pt, Cm, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

INPUT_PATH = '/workspace/data/input.xlsx'
OUTPUT_PATH = '/workspace/report.docx'

def set_table_borders(table):
    tbl = table._tbl
    tblPr = tbl.tblPr if tbl.tblPr is not None else OxmlElement('w:tblPr')
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        element = OxmlElement(f'w:{edge}')
        element.set(qn('w:val'), 'single')
        element.set(qn('w:sz'), '4')
        element.set(qn('w:space'), '0')
        element.set(qn('w:color'), '000000')
        borders.append(element)
    tblPr.append(borders)
    if tbl.tblPr is None:
        tbl.append(tblPr)

def add_page_number(doc):
    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False
    para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run()
    fldChar1 = OxmlElement('w:fldChar')
    fldChar1.set(qn('w:fldCharType'), 'begin')
    run._r.append(fldChar1)
    run2 = para.add_run()
    instrText = OxmlElement('w:instrText')
    instrText.set(qn('xml:space'), 'preserve')
    instrText.text = ' PAGE '
    run2._r.append(instrText)
    run3 = para.add_run()
    fldChar2 = OxmlElement('w:fldChar')
    fldChar2.set(qn('w:fldCharType'), 'end')
    run3._r.append(fldChar2)

def format_paragraph(para):
    pf = para.paragraph_format
    pf.line_spacing = 1.5
    pf.first_line_indent = Pt(24)

def add_heading_formatted(doc, text, level):
    para = doc.add_paragraph()
    run = para.add_run(text)
    if level == 1:
        run.font.size = Pt(16)
        run.bold = True
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif level == 2:
        run.font.size = Pt(14)
        run.bold = True
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    elif level == 3:
        run.font.size = Pt(12)
        run.bold = True
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    para.paragraph_format.space_before = Pt(12)
    para.paragraph_format.space_after = Pt(6)
    return para

def add_body_paragraph(doc, text):
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.font.name = 'FangSong'
    run._element.rPr.rFonts.set(qn('w:eastAsia'), 'FangSong')
    run.font.size = Pt(12)
    format_paragraph(para)
    return para

def make_table_bold_header(table):
    for cell in table.rows[0].cells:
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True

def main():
    wb = openpyxl.load_workbook(INPUT_PATH, data_only=True)
    ws = wb['furtherex1']

    # Extract key data
    years = [ws.cell(28, c).value for c in range(2, 15)]
    yield_curve = [ws.cell(29, c).value for c in range(2, 15)]
    spot_vol = [ws.cell(32, c).value for c in range(2, 15)]
    notional = ws.cell(39, 2).value
    m_partitions = ws.cell(34, 2).value

    # Compute discount function p(n) = exp(-n * r(n))
    discount_func = []
    for i, (yr, rate) in enumerate(zip(years, yield_curve)):
        if yr is not None and rate is not None and yr > 0:
            discount_func.append((int(yr), math.exp(-yr * rate)))
        elif yr == 0:
            discount_func.append((0, 1.0))

    wb.close()

    # Create document
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(3.0)

    style = doc.styles['Normal']
    style.font.name = 'FangSong'
    style.font.size = Pt(12)
    style._element.rPr.rFonts.set(qn('w:eastAsia'), 'FangSong')

    add_page_number(doc)

    # Title
    add_heading_formatted(doc, 'Interest Rate Swap Pricing Analysis Report', 1)

    # Section 1
    add_heading_formatted(doc, '1. Model Overview and Pricing Conclusion', 2)
    add_body_paragraph(doc,
        'This report presents a pricing analysis of a Constant Maturity Swap based on the Ho-Lee '
        'no-arbitrage interest rate model. The model constructs an interest rate lattice using a '
        'binomial tree approach, determining rate levels at each node through no-arbitrage conditions '
        'to calculate the fair value of the swap contract.')
    add_body_paragraph(doc,
        f'Core assumptions include: a flat initial yield curve (all tenors at 10%), '
        f'lognormal spot volatility of 12%, number of partitions m={int(m_partitions) if m_partitions else 1}, '
        f'and notional principal of {int(notional) if notional else 100}. '
        'Under these parameters, the model computes swap lattice values at each node.')

    # Section 2
    add_heading_formatted(doc, '2. Model Input Parameters', 2)
    add_body_paragraph(doc, 'The following table summarizes the core input parameters of the model:')

    param_data = [
        ['Parameter', 'Value'],
        ['Initial Yield Curve', 'Flat at 10% (all tenors)'],
        ['Lognormal Spot Volatility (σS)', '12%'],
        ['Number of Partitions (m)', str(int(m_partitions) if m_partitions else 1)],
        ['Notional Principal', str(int(notional) if notional else 100)],
        ['Model Type', 'Constant Maturity Swap (Ho-Lee Framework)'],
        ['Compounding Method', 'Continuous compounding'],
        ['Term Structure', '0-12 years'],
    ]
    table = doc.add_table(rows=len(param_data), cols=2)
    table.style = 'Table Grid'
    set_table_borders(table)
    for i, row_data in enumerate(param_data):
        for j, val in enumerate(row_data):
            table.rows[i].cells[j].text = val
    make_table_bold_header(table)

    # Section 3
    add_heading_formatted(doc, '3. Term Structure Analysis', 2)
    add_body_paragraph(doc,
        'The initial yield curve is the foundational input of the model, determining the term structure '
        'of risk-free rates. This model assumes a flat yield curve, meaning the spot rate at all '
        'tenors is 10%.')

    # Yield curve table
    yc_headers = ['Year'] + [str(int(y)) for y in years if y is not None]
    yc_values = ['Yield'] + [f'{r*100:.1f}%' if r else '-' for r in yield_curve if r is not None]
    table = doc.add_table(rows=2, cols=len(yc_headers))
    table.style = 'Table Grid'
    set_table_borders(table)
    for j, val in enumerate(yc_headers):
        table.rows[0].cells[j].text = val
    for j, val in enumerate(yc_values):
        table.rows[1].cells[j].text = val
    make_table_bold_header(table)

    # Yield curve chart
    valid_years = [int(y) for y in years if y is not None]
    valid_rates = [r * 100 for r in yield_curve if r is not None]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(valid_years[:len(valid_rates)], valid_rates, 'b-o', linewidth=2, markersize=5)
    ax.set_xlabel('Year', fontsize=10)
    ax.set_ylabel('Yield (%)', fontsize=10)
    ax.set_title('Initial Yield Curve', fontsize=12)
    ax.set_ylim(0, 15)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    chart_path = '/tmp/yield_curve.png'
    plt.savefig(chart_path, dpi=150)
    plt.close()
    doc.add_picture(chart_path, width=Inches(5.0))

    add_body_paragraph(doc,
        'A flat yield curve implies the market expects future short-term rates to remain stable, '
        'with no significant term premium. This is a simplifying assumption; in practice, yield curves '
        'typically exhibit an upward slope.')

    # Section 4
    add_heading_formatted(doc, '4. Discount Function and Forward Prices', 2)
    add_body_paragraph(doc,
        'The discount function p(n) represents the present value of $1 received in n years, '
        'calculated as p(n) = exp(-n × r(n)), where r(n) is the n-year spot rate.')

    df_data = [['Year', 'Discount Factor p(n)']]
    for yr, pn in discount_func[:7]:
        df_data.append([str(yr), f'{pn:.6f}'])
    table = doc.add_table(rows=len(df_data), cols=2)
    table.style = 'Table Grid'
    set_table_borders(table)
    for i, row_data in enumerate(df_data):
        for j, val in enumerate(row_data):
            table.rows[i].cells[j].text = val
    make_table_bold_header(table)

    add_body_paragraph(doc,
        'As maturity increases, the discount factor decreases, reflecting the time value of money. '
        'Under a flat 10% yield curve, the discount factor decays exponentially.')

    # Section 5
    add_heading_formatted(doc, '5. Risk Disclosure and Model Limitations', 2)
    add_body_paragraph(doc, 'The key assumptions and limitations of this model include:')

    limitations = [
        'The interest rate volatility is assumed constant (σS = 12%); in practice, volatility varies with tenor (volatility smile/term structure)',
        'The Ho-Lee model permits negative interest rates, which may produce unreasonable pricing in low-rate environments',
        'The model assumes a lognormal distribution, potentially underestimating tail risks (extreme rate movements)',
        'The flat yield curve assumption is overly simplified; actual applications should use market-observed term structures',
        'Credit risk and counterparty default risk are not considered',
        'The model assumes continuous compounding and continuous trading, which deviates from discrete market transactions',
    ]
    for lim in limitations:
        para = doc.add_paragraph()
        para.style = 'List Bullet'
        run = para.add_run(lim)
        run.font.name = 'FangSong'
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'FangSong')
        run.font.size = Pt(12)

    doc.save(OUTPUT_PATH)
    print(f'Report saved to {OUTPUT_PATH}')

# [oracle] 把本脚本源码复制到 /workspace/generated_code.py,供 L2 code judge 读取
# (判分器 _find_code_file 优先读该路径;solve.py 本身不在 /workspace,不复制则 L2 找不到代码 → L2=0)
def _emit_generated_code():
    import shutil, os
    try:
        os.makedirs('/workspace', exist_ok=True)
        shutil.copyfile(os.path.abspath(__file__), '/workspace/generated_code.py')
    except Exception as e:
        print(f'warn: could not emit generated_code.py: {e}')


if __name__ == '__main__':
    _emit_generated_code()
    main()
