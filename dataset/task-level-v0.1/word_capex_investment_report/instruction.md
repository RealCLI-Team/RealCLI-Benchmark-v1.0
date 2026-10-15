# Capital Investment Decision Analysis Report — Excel to Word

## Task Overview

The finance department has completed a capital expenditure DCF (Discounted Cash Flow) valuation model for Newtech Limited, currently stored in Excel format. You need to extract the key data and analysis results into a well-structured, professionally formatted Word document for submission to the board of directors and investment committee for project approval.

Your task is to read the financial data from the Excel workbook and generate a professional investment decision analysis report, saving the document to the specified output path.

## Input File

- **`/workspace/data/input.xlsx`** — Contains the complete DCF valuation model with the following worksheets:
  - `Projected IS`: Projected income statement (2008-2013), including revenue, costs, expenses, and net income
  - `WACC`: Weighted average cost of capital calculation, including cost of equity, cost of debt, and weights
  - `DCF Model`: Discounted cash flow model, including net income, working capital changes, depreciation, capex, and free cash flows
  - `Share Value`: Equity value calculation
  - `Questions`: Valuation assumption questions

## Output Requirements

Create and write the investment decision analysis report at **`/workspace/report.docx`**.

Additionally, save the Python script you used to generate the report at **`/workspace/generated_code.py`**.
This script is part of the deliverable and is graded: it must be the actual, runnable code that
reads the Excel input and produces `/workspace/report.docx` (not a summary or a stub).

## Report Content Requirements

The report should contain the following sections, each with a level-2 heading:

### 1. Project Overview and Investment Conclusion
- Provide a paragraph describing Newtech Limited (Canadian technology company, software + contract services)
- State the investment valuation conclusion (enterprise value, equity value, and other key figures from the WACC sheet)

### 2. Weighted Average Cost of Capital (WACC) Analysis
- Include a WACC calculation table extracted from the `WACC` worksheet:
  - Required return on equity (15%)
  - Company risk premium (10%)
  - Cost of equity (25%)
  - Cost of debt (6%), after-tax cost of debt (4%)
  - Equity weight (80%), debt weight (20%)
  - WACC (20.8%)
- Table should have at least 2 columns (Item, Value) and at least 7 rows

### 3. Projected Income Statement Summary
- Include an income statement summary table from the `Projected IS` worksheet for 2009-2013:
  - Rows: Revenue, Cost of Sales, Gross Profit, Net Income
  - Columns: 2009, 2010, 2011, 2012, 2013
- Table should have at least 5 columns and at least 5 rows

### 4. Free Cash Flow and Valuation
- Include a DCF table from the `DCF Model` worksheet:
  - Rows: Net Income, Working Capital Changes, Depreciation, Capital Expenditure, Free Cash Flow, Present Value
  - Columns: Each year from 2009-2013
- Table should have at least 6 columns and at least 6 rows
- Include a paragraph after the table explaining the terminal value calculation method (perpetuity approach)

### 5. Investment Value and Risk Factors
- Summarize enterprise value in text (from the WACC sheet Value of Firm data)
- List key assumptions and risk factors (e.g., revenue growth assumptions, WACC sensitivity)
- Embed a free cash flow trend chart (using matplotlib to plot the 2009-2013 Free Cash Flows as a bar or line chart)

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
7. **Number Format**: Amounts to 2 decimal places, percentages to 1 decimal place

## Environment and Constraints

- **Network Access**: Not required and should not be used
- **Available Tools**: Python environment with pre-installed `openpyxl` (read Excel), `python-docx` (generate Word), `matplotlib` (chart generation), `Pillow` (image processing)
- **Input File**: Data is complete; read directly from Excel without additional computation

## Evaluation Criteria

Your submission will be evaluated on: (1) structural correctness of the document (page size, margins, fonts, headings) — 40%, (2) quality and correctness of the generating code at `/workspace/generated_code.py` — 30%, and (3) visual quality and professional appearance of the rendered document — 30%. The final score is the weighted sum of the three (a continuous value, not pass/fail).
