# Merger & LBO Transaction Analysis Report — Excel to Word

## Task Overview

The corporate development team has completed a comprehensive M&A analysis for Apple Inc.'s proposed acquisition of a technology target, including strategic merger consequences analysis and a leveraged buyout (LBO) alternative scenario. The complete deal model is stored in Excel format with WACC calculation, DCF valuation, comparable company analysis, precedent transaction benchmarks, merger accretion/dilution, and LBO returns.

Your task is to read the deal data from the Excel workbook and generate a professional M&A transaction analysis report, saving the document to the specified output path. The report will be submitted to the investment committee and board of directors for deal approval.

## Input File

- **`/workspace/data/input.xlsx`** — Contains the complete deal model with the following worksheets:
  - `Merger-Model`: Transaction assumptions, purchase price allocation, buyer/seller/combined income statements, accretion/dilution analysis
  - `Basic-LBO`: LBO transaction structure, sources & uses, debt tranches, investor returns and IRR
  - `DCF`: Discounted cash flow valuation for Apple, sensitivity analysis
  - `WACC`: Weighted average cost of capital with comparable company betas
  - `Public Comps`: Trading multiples for comparable public companies
  - `M&A-Comps`: Precedent transaction multiples
  - `ValSum`: Consolidated valuation football-field summary

## Output Requirements

Create and write the M&A transaction analysis report at **`/workspace/report.docx`**.

Additionally, save the Python script you used to generate the report at **`/workspace/generated_code.py`**.
This script is part of the deliverable and is graded: it must be the actual, runnable code that
reads the Excel input and produces `/workspace/report.docx` (not a summary or a stub).

## Report Content Requirements

The report should contain the following sections, each with a level-2 heading:

### 1. Deal Overview and Transaction Summary
- Provide a paragraph describing the strategic rationale for the acquisition (Apple as the buyer expanding its portfolio)
- State the deal headline metrics from the `Merger-Model` sheet:
  - Per Share Purchase Price ($75.00)
  - Consideration mix: % Cash, % Debt, % Stock
  - Equity Purchase Price, Debt Issued, New Shares Issued
- Include an assessment of the combined entity's projected performance

### 2. Purchase Price and Goodwill Allocation
- Include a goodwill calculation table extracted from the `Merger-Model` worksheet:
  - Equity Purchase Price ($43,048.9M)
  - Less: Seller Book Value
  - Plus: Write-Offs of Deferred Tax
  - Total Allocable Purchase Premium
  - Less: PP&E and Intangibles Write-Ups
  - Total Goodwill Created ($30,974.7M)
- Table should have at least 2 columns (Item, Value) and at least 6 rows

### 3. Combined Income Statement and Accretion / Dilution
- Include a combined income statement summary from the `Merger-Model` worksheet for FY 2010E–FY 2011E:
  - Rows: Combined Revenue, Revenue Synergies, Gross Profit, Operating Income, Net Income, EPS
  - Columns: FY 2010E, FY 2011E
- Report the earnings accretion / (dilution) figures:
  - FY 2010E: $0.68 accretion, +6% relative
  - FY 2011E: $1.23 accretion, +9% relative
- Table should have at least 2 columns and at least 5 rows

### 4. WACC and DCF Valuation
- Include a WACC calculation table extracted from the `WACC` worksheet:
  - Levered Beta from comparable companies
  - Unlevered Beta (median ~1.16)
  - Apple relevered Beta
  - Cost of Equity, Cost of Debt
  - WACC (~12.5%)
- Include a DCF valuation summary from the `DCF` worksheet for FY 2010E–FY 2012E:
  - Rows: Revenue, EBITDA, Unlevered Free Cash Flow, Present Value of FCF
- Add a paragraph explaining the DCF methodology (mid-year discounting convention, terminal value with EBITDA exit multiple)

### 5. LBO Alternative Structure and Returns
- Include an LBO Sources & Uses table from the `Basic-LBO` worksheet:
  - Sources: Bank Debt ($79,767.5M), High-Yield Debt ($26,589.2M), Investor Equity ($106,463.1M), Total Sources
  - Uses: Equity Value of Target, Advisory Fees, Financing Fees, Total Uses
- Report LBO deal metrics:
  - Offer Premium 20%, Offer Price per Share $230.47
  - Enterprise Value $177,318.4M
  - EBITDA Purchase Multiple, EBITDA Exit Multiple
  - Debt / Equity split (50% / 50%)
- Report the projected investor IRR (16.7%) from the `Basic-LBO` sheet
- Embed a debt tranche composition chart (using matplotlib to plot Bank Debt vs High-Yield Debt vs Investor Equity as a pie or bar chart)

### 6. Sensitivity Analysis and Risk Factors
- Reference the DCF sensitivity tables (varying discount rate 10-12% and terminal EBITDA multiple 4x-9x)
- Reference the LBO sensitivity tables (varying purchase premium and exit multiple / debt %)
- List key deal risks: financing risk, integration risk, synergy realization risk, market conditions
- Note that projected synergies (10% revenue synergies, 10% cost synergies) are subject to execution risk

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
7. **Number Format**: Dollar amounts in millions to 1 decimal place, percentages to 1 decimal place, per-share amounts to 2 decimal places, multiples to 1 decimal place

## Environment and Constraints

- **Network Access**: Not required and should not be used
- **Available Tools**: Python environment with pre-installed `openpyxl` (read Excel), `python-docx` (generate Word), `matplotlib` (chart generation), `Pillow` (image processing)
- **Input File**: All financial data is complete in the input.xlsx (fully computed merger and LBO model with real values); read directly from Excel without additional computation

## Evaluation Criteria

Your submission will be evaluated on: (1) structural correctness of the document (page size, margins, fonts, headings) — 40%, (2) quality and correctness of the generating code at `/workspace/generated_code.py` — 30%, and (3) visual quality and professional appearance of the rendered document — 30%. The final score is the weighted sum of the three (a continuous value, not pass/fail).
