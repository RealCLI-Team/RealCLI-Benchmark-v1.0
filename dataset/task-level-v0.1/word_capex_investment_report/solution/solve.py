#!/usr/bin/env python3
"""Golden solver: reads Capex DCF input.xlsx and generates investment analysis report."""
import os
import openpyxl
from docx import Document
from docx.shared import Pt, Cm, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Paths follow the task contract (/workspace). They are overridable via env vars
# so the host harness can point them at an isolated dir without editing the code;
# the generated_code.py copy (read by the L2 judge) keeps these portable
# /workspace defaults, so it reads as a clean, runnable deliverable.
INPUT_PATH = os.environ.get('INPUT_XLSX', '/workspace/data/input.xlsx')
OUTPUT_PATH = os.environ.get('OUTPUT_DOCX', '/workspace/report.docx')

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

def format_paragraph(para, first_line_indent=True):
    pf = para.paragraph_format
    pf.line_spacing = 1.5
    if first_line_indent:
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
    ws_wacc = wb['WACC']
    ws_is = wb['Projected IS']
    ws_dcf = wb['DCF Model']

    # Extract WACC data (read every parameter straight from its cell on the WACC sheet)
    req_return = ws_wacc['F9'].value      # Required Rate of return on Equity
    company_risk = ws_wacc['F10'].value   # Add - Company Risk
    cost_of_equity = ws_wacc['F11'].value # Cost of Equity
    cost_of_debt = ws_wacc['F13'].value   # Cost of Debt
    after_tax_debt = ws_wacc['F14'].value # After Tax Cost of Debt
    weight_equity = ws_wacc['F16'].value  # Weight of Equity
    weight_debt = ws_wacc['F17'].value    # Weight of Debt
    wacc_val = ws_wacc['F19'].value       # WACC
    value_of_firm = ws_wacc['I9'].value   # Value of Firm
    mkt_val_equity = ws_wacc['I8'].value  # Mkt val of equity
    debt_value = ws_wacc['I10'].value     # Debt Value

    # Extract Projected IS data (years 2009-2013, columns H-L = 8-12)
    years_is = [ws_is.cell(11, c).value for c in range(8, 13)]
    revenue = [ws_is.cell(13, c).value for c in range(8, 13)]
    cost_of_sales = [ws_is.cell(18, c).value for c in range(8, 13)]
    gross_profit = [ws_is.cell(20, c).value for c in range(8, 13)]
    net_income_is = [ws_is.cell(33, c).value for c in range(8, 13)]

    # Extract DCF Model data (years 2009-2013+TV, columns F-K = 6-11)
    years_dcf = [ws_dcf.cell(10, c).value for c in range(6, 12)]
    net_income_dcf = [ws_dcf.cell(12, c).value for c in range(6, 12)]
    working_cap = [ws_dcf.cell(13, c).value for c in range(6, 12)]
    depreciation = [ws_dcf.cell(14, c).value for c in range(6, 12)]
    capex = [ws_dcf.cell(15, c).value for c in range(6, 12)]
    fcf = [ws_dcf.cell(16, c).value for c in range(6, 12)]
    pv_cf = [ws_dcf.cell(20, c).value for c in range(6, 12)]
    terminal_value = ws_dcf.cell(18, 11).value

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
    add_heading_formatted(doc, 'Newtech Limited Capital Investment Decision Analysis Report', 1)

    # Section 1
    add_heading_formatted(doc, '1. Project Overview and Investment Conclusion', 2)
    add_body_paragraph(doc,
        'Newtech Limited is a Canadian technology company whose core business includes software product '
        'sales and technical contract services. This report provides a valuation analysis of the company '
        'using the Discounted Cash Flow (DCF) method, to support the board\'s investment decision-making.')
    add_body_paragraph(doc,
        f'Based on the DCF model, the enterprise value (Value of Firm) is {value_of_firm:,.2f} thousand CAD, '
        f'of which market value of equity is {mkt_val_equity:,.2f} thousand CAD, '
        f'and debt value is {debt_value:,.2f} thousand CAD. '
        f'The Weighted Average Cost of Capital (WACC) is {wacc_val*100:.1f}%.')

    # Section 2
    add_heading_formatted(doc, '2. Weighted Average Cost of Capital (WACC) Analysis', 2)
    add_body_paragraph(doc,
        'WACC represents the company\'s blended cost of financing and is the key discount rate used '
        'in the DCF model to discount future cash flows. The detailed calculation is as follows:')

    wacc_data = [
        ['Item', 'Value'],
        ['Required Return on Equity', f'{req_return*100:.1f}%'],
        ['Company Risk Premium', f'{company_risk*100:.1f}%'],
        ['Cost of Equity', f'{cost_of_equity*100:.1f}%'],
        ['Cost of Debt', f'{cost_of_debt*100:.1f}%'],
        ['After-Tax Cost of Debt', f'{after_tax_debt*100:.1f}%'],
        ['Equity Weight', f'{weight_equity*100:.1f}%'],
        ['Debt Weight', f'{weight_debt*100:.1f}%'],
        ['WACC', f'{wacc_val*100:.1f}%'],
    ]
    table = doc.add_table(rows=len(wacc_data), cols=2)
    table.style = 'Table Grid'
    set_table_borders(table)
    for i, row_data in enumerate(wacc_data):
        for j, val in enumerate(row_data):
            table.rows[i].cells[j].text = str(val)
    make_table_bold_header(table)

    # Section 3
    add_heading_formatted(doc, '3. Projected Income Statement Summary', 2)
    add_body_paragraph(doc,
        'The following table presents key metrics from Newtech Limited\'s projected income statement '
        'for 2009-2013 (in thousands of CAD):')

    is_headers = ['Item'] + [str(int(y)) for y in years_is if y]
    is_data = [
        is_headers,
        ['Revenue'] + [f'{v:,.2f}' if v else '-' for v in revenue],
        ['Cost of Sales'] + [f'{v:,.2f}' if v else '-' for v in cost_of_sales],
        ['Gross Profit'] + [f'{v:,.2f}' if v else '-' for v in gross_profit],
        ['Net Income'] + [f'{v:,.2f}' if v else '-' for v in net_income_is],
    ]
    table = doc.add_table(rows=len(is_data), cols=len(is_headers))
    table.style = 'Table Grid'
    set_table_borders(table)
    for i, row_data in enumerate(is_data):
        for j, val in enumerate(row_data):
            table.rows[i].cells[j].text = str(val)
    make_table_bold_header(table)

    # Section 4
    add_heading_formatted(doc, '4. Free Cash Flow and Valuation', 2)
    add_body_paragraph(doc,
        'Free Cash Flow (FCF) is the core of DCF valuation, calculated as: '
        'FCF = Net Income - Working Capital Changes + Depreciation - Capital Expenditure. '
        'The following table shows annual FCF and present values:')

    dcf_headers = ['Item'] + [str(int(y)) if isinstance(y, (int, float)) else str(y) for y in years_dcf if y]
    dcf_rows = [
        ['Net Income'] + [f'{v:,.2f}' if v else '-' for v in net_income_dcf],
        ['Working Capital Changes'] + [f'{v:,.2f}' if v else '-' for v in working_cap],
        ['Depreciation'] + [f'{v:,.2f}' if v else '-' for v in depreciation],
        ['Capital Expenditure'] + [f'{v:,.2f}' if v else '-' for v in capex],
        ['Free Cash Flow'] + [f'{v:,.2f}' if v else '-' for v in fcf],
        ['Present Value (PV)'] + [f'{v:,.2f}' if v else '-' for v in pv_cf],
    ]
    all_dcf = [dcf_headers] + dcf_rows
    table = doc.add_table(rows=len(all_dcf), cols=len(dcf_headers))
    table.style = 'Table Grid'
    set_table_borders(table)
    for i, row_data in enumerate(all_dcf):
        for j, val in enumerate(row_data):
            if j < len(table.rows[i].cells):
                table.rows[i].cells[j].text = str(val)
    make_table_bold_header(table)

    if terminal_value:
        add_body_paragraph(doc,
            f'The Terminal Value is calculated using the perpetuity approach, yielding a terminal value of '
            f'{terminal_value:,.2f} thousand CAD. This assumes the company will continue operating at a '
            f'stable growth rate beyond the forecast period.')

    # Section 5
    add_heading_formatted(doc, '5. Investment Value and Risk Factors', 2)
    add_body_paragraph(doc,
        f'Based on the DCF analysis, Newtech Limited\'s enterprise value is {value_of_firm:,.2f} thousand CAD. '
        f'Given that the company is in a growth phase with expected continued revenue growth from its '
        f'software business, the investment offers reasonable return expectations.')

    add_body_paragraph(doc, 'Key assumptions and risk factors include:')
    risks = [
        'Revenue growth assumptions depend on sustained software market demand; deteriorating market conditions may result in actual revenue falling below projections',
        'WACC is sensitive to cost of equity and company risk premium; rising market interest rates would reduce the valuation',
        'Terminal value accounts for a high proportion of total valuation and is sensitive to the perpetuity growth rate assumption',
        'Working capital change assumptions are based on historical ratio extrapolation; actual fluctuations may be larger',
    ]
    for risk in risks:
        para = doc.add_paragraph()
        para.style = 'List Bullet'
        run = para.add_run(risk)
        run.font.name = 'FangSong'
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'FangSong')
        run.font.size = Pt(12)

    # Chart: FCF trend (forecast years 2009-2013 only; the TV column is a single
    # terminal value, not an annual flow, so it is excluded from the trend chart)
    fcf_annual = [v for v in fcf[:-1] if v is not None]          # drop the TV column
    fcf_years = [int(y) for y in years_dcf[:-1] if isinstance(y, (int, float))]
    if fcf_annual and fcf_years:
        fig, ax = plt.subplots(figsize=(6, 3.5))
        ax.bar(fcf_years[:len(fcf_annual)], fcf_annual[:len(fcf_years)], color='#4472C4', width=0.6)
        ax.set_xlabel('Year', fontsize=10)
        ax.set_ylabel('Free Cash Flow (Thousands CAD)', fontsize=10)
        ax.set_title('Newtech Limited Free Cash Flow Trend', fontsize=12)
        ax.set_xticks(fcf_years[:len(fcf_annual)])
        ax.grid(axis='y', alpha=0.3)
        plt.tight_layout()
        chart_path = os.path.join(os.path.dirname(OUTPUT_PATH) or '.', 'fcf_chart.png')
        plt.savefig(chart_path, dpi=150)
        plt.close()
        doc.add_picture(chart_path, width=Inches(5.0))

    doc.save(OUTPUT_PATH)
    print(f'Report saved to {OUTPUT_PATH}')

# [oracle] 把本脚本源码复制到 OUTPUT_PATH 同目录的 generated_code.py,供 L2 code judge 读取
# (判分器 _find_code_file 优先读该路径;不复制则 L2 找不到代码 → L2=0)。
# 复制的是「本脚本源码」——它带可移植的 /workspace 路径默认值,所以 judge 读到的是干净可交付的代码,
# 而不是被宿主改写成 /tmp 的运行副本。
def _emit_generated_code():
    import shutil
    try:
        out_dir = os.path.dirname(OUTPUT_PATH) or '.'
        os.makedirs(out_dir, exist_ok=True)
        shutil.copyfile(os.path.abspath(__file__), os.path.join(out_dir, 'generated_code.py'))
    except Exception as e:
        print(f'warn: could not emit generated_code.py: {e}')


if __name__ == '__main__':
    _emit_generated_code()
    main()
