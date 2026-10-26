# Interest Rate Swap Pricing Analysis Report — Excel to Word

## Task Overview

The quantitative analysis team has completed an interest rate swap pricing model, currently stored in Excel format. The model contains an interest rate lattice, discount functions, forward prices, and swap lattice calculations. You need to extract the analysis results and generate a well-structured Word document for submission to the risk management committee.

Your task is to read the model data from the Excel workbook and generate an interest rate swap pricing analysis report, saving the document to the specified output path.

## Input File

- **`/workspace/data/input.xlsx`** — Contains the complete swap pricing model with the following worksheets:
  - `numex`: Numerical examples and base parameters
  - `furtherex1`: Core calculations — initial yield curve, discount function, forward prices, interest rate lattice, swap lattice (primary worksheet)
  - `furtherex2-1`, `furtherex2-2`: Extended calculations

  Key data regions in the `furtherex1` worksheet:
  - Rows 28-29: Years (0-12) and initial yield curve (all at 10%)
  - Row 32: Lognormal spot volatility (σS = 12%)
  - Row 34: Number of partitions (m=1)
  - Row 39: Notional principal (100)
  - Rows 47-48: Swap lattice results
  - Rows 62-80: Discount function lattice and interest rate lattice

## Output Requirements

Create and write the interest rate swap pricing analysis report at **`/workspace/report.docx`**.

Additionally, save the Python script you used to generate the report at **`/workspace/generated_code.py`**.
This script is part of the deliverable and is graded: it must be the actual, runnable code that
reads the Excel input and produces `/workspace/report.docx` (not a summary or a stub).

## Report Content Requirements

The report should contain the following sections, each with a level-2 heading:

### 1. Model Overview and Pricing Conclusion
- Describe the model: constant maturity swap pricing based on the Ho-Lee no-arbitrage interest rate model
- List core assumptions: initial yield curve (flat at 10%), spot volatility (12%), notional principal (100)
- State the pricing conclusion (fair value of the swap)

### 2. Model Input Parameters
- Include a parameter table extracted from the `furtherex1` worksheet:
  - Initial yield curve values (at each tenor point)
  - Lognormal spot volatility (σS)
  - Number of partitions m
  - Notional principal
- Table should have at least 2 columns (Parameter Name, Value) and at least 5 rows

### 3. Term Structure Analysis
- Include a yield curve table (years 0-12 with corresponding initial yields)
- Embed a yield curve chart (using matplotlib: x-axis = years 0-12, y-axis = yield rate)
- Include a paragraph explaining the implications of a flat yield curve

### 4. Discount Function and Forward Prices
- Include a discount function table (year vs. discount factor values)
- Include a paragraph explaining the discount function p(n) calculation: p(n) = exp(-n × r(n))

### 5. Risk Disclosure and Model Limitations
- List the model's key assumptions and limitations in structured paragraphs
- Include: constant volatility assumption, underestimation of tail risk, market liquidity risk, etc.

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
7. **Number Format**: Interest rates to 4 decimal places or as percentages, amounts to 2 decimal places

## Environment and Constraints

- **Network Access**: Not required and should not be used
- **Available Tools**: Python environment with pre-installed `openpyxl` (read Excel), `python-docx` (generate Word), `matplotlib` (chart generation), `Pillow` (image processing)
- **Input File**: Data is complete; read directly from Excel without additional computation

## Evaluation Criteria

Your submission will be evaluated on: (1) structural correctness of the document (page size, margins, fonts, headings) — 40%, (2) quality and correctness of the generating code at `/workspace/generated_code.py` — 30%, and (3) visual quality and professional appearance of the rendered document — 30%. The final score is the weighted sum of the three (a continuous value, not pass/fail).
