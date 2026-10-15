# LBO Investment Return Analysis Report — Excel to Word

## Task Overview

The private equity team has completed a Leveraged Buyout (LBO) model with IRR return calculations, currently stored in Excel format. The model contains the deal structure, multi-period cash flow projections, and Internal Rate of Return (IRR) calculations. You need to compile the key data and return analysis into a well-formatted Word document for the investment committee's project approval.

Your task is to read the LBO model data from the Excel workbook and generate an investment return analysis report, saving the document to the specified output path.

## Input File

- **`/workspace/data/input.xlsx`** — Contains the complete LBO model with the following worksheets:
  - `Input`: Deal assumption inputs (stock price, offer premium, shares, debt structure, interest rates, etc.)
  - `Inc_St`: Projected income statement
  - `Cash_Flow`: Cash flow statement
  - `IRR_Returns`: IRR/return calculations (core worksheet) — cash flow allocation, interest income, repayments, and IRR
  - `WACC`: Weighted average cost of capital
  - `DCF_Analysis`: DCF analysis
  - Other supporting sheets: `Bal_Sheet`, `Depr_Amort`, `Income_Tax`, `Ratios`

  Key parameters in the `Input` worksheet:
  - Current stock price: $6, offer premium: 10%
  - Shares outstanding: 100,000
  - Assumed debt: $65,000, cash infusion: $27,500
  - Coupon rate: 4.25%, maturity: 9 years

  Key regions in the `IRR_Returns` worksheet:
  - Exit year: 2018
  - Rows 21-30: Cash Flows & IRR (period cash flow summary + IRR)
  - Rows 25-30: Initial Investment, Cash Interest, Repayment, Principal, Participation on Exit, IRR

## Output Requirements

Create and write the LBO investment return analysis report at **`/workspace/report.docx`**.

Additionally, save the Python script you used to generate the report at **`/workspace/generated_code.py`**.
This script is part of the deliverable and is graded: it must be the actual, runnable code that
reads the Excel input and produces `/workspace/report.docx` (not a summary or a stub).

## Report Content Requirements

The report should contain the following sections, each with a level-2 heading:

### 1. Deal Overview and Return Summary
- Describe the LBO transaction in paragraphs
- List key deal parameters: target company stock price, offer premium, acquisition price, financing structure
- State the IRR return conclusion

### 2. Deal Structure and Financing Arrangement
- Include a deal structure table extracted from the `Input` worksheet:
  - Current stock price, offer premium, offer price per share
  - Shares outstanding, equity purchase price
  - Assumed debt, transaction costs, cash infusion
  - Total funding requirements
- Table should have at least 2 columns (Item, Amount) and at least 8 rows

### 3. Debt Terms
- Include a debt terms table extracted from the `Input` worksheet:
  - Maturity years, annual repayment, coupon rate
  - Average interest, optional prepayment
  - Revolving credit facility, undrawn commitment fee
- Table should have at least 2 columns (Item, Value) and at least 6 rows

### 4. Cash Flow and IRR Analysis
- Include a multi-period cash flow table extracted from rows 21-30 of the `IRR_Returns` worksheet:
  - Rows: Initial Investment, Cash Interest, Repayment, Principal, Participation on Exit
  - Columns: Each period (Post-Acquisition periods 1-10)
- Table should have at least 6 columns (row label + 5 periods) and at least 5 rows
- Embed a cash flow composition chart (using matplotlib to plot a grouped bar chart or line chart of period cash flows)

### 5. Exit Strategy and Sensitivity Analysis
- Include paragraphs describing the exit year (2018) and exit method assumptions
- Discuss key factors affecting IRR (exit timing, exit multiples, debt repayment schedule)
- List risk factors

## Formatting Requirements

1. **Page Setup**: A4 paper (21.0cm × 29.7cm), top/bottom margins 2.54cm, left/right margins 3.0cm
2. **Body Font**: FangSong (仿宋), 12pt
3. **Heading Format**:
   - Level 1 (report title): 16pt, bold, center-aligned
   - Level 2 (section headings): 14pt, bold, left-aligned
   - Level 3 (subsections): 12pt, bold, left-aligned
4. **Paragraph Format**: 1.5x line spacing, first-line indent of 24pt
5. **Table Format**: Bold header row, bordered cells
6. **Page Numbers**: Add page numbers in the footer
7. **Number Format**: Amounts to 2 decimal places (in thousands), percentages to 2 decimal places

## Environment and Constraints

- **Network Access**: Not required and should not be used
- **Available Tools**: Python environment with pre-installed `openpyxl` (read Excel), `python-docx` (generate Word), `matplotlib` (chart generation), `Pillow` (image processing)
- **Input File**: Data is complete; read directly from Excel without additional computation

## Evaluation Criteria

Your submission will be evaluated on: (1) structural correctness of the document (page size, margins, fonts, headings) — 40%, (2) quality and correctness of the generating code at `/workspace/generated_code.py` — 30%, and (3) visual quality and professional appearance of the rendered document — 30%. The final score is the weighted sum of the three (a continuous value, not pass/fail).
