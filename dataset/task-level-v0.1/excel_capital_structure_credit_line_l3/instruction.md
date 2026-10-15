    # Credit-line decision model

    ## Background

    You are given a partially-completed financial model in an Excel workbook.
    Credit-line decision model: expected profit/loss under default and normalized grant/deny decision weights across two sheets.
    The target line-items have been left blank in the input; your job is to reconstruct them
    from the surrounding assumptions and existing rows, exactly as a financial analyst would.

    ## Input File

    - `/workspace/data/input.xlsx` — the workbook to complete. All assumptions, drivers, labels, and
      the sheet/row layout are already present. Only the target line-items (described below) are blank.

    ## Task

    Complete the financial model based on the provided assumptions. Ensure the existing structure, layout, and formatting of the model are preserved throughout the process.
In the Credit Line sheet, compute the "However, default is expected to occur", "Overall expected profit or (loss)", "Thus, expected gain or (loss) on line & collateral" row(s) from the given input rows, keeping every input and label unchanged. In the Credit Decision sheet, compute the "Relative weight for denying credit", "Relative weight for granting credit" row(s) from the given input rows, keeping every input and label unchanged.

    ## Deliverable

    Write your completed workbook to **`/workspace/output.xlsx`**.

    Requirements:
    - Start from `/workspace/data/input.xlsx` and fill **only** the target cells in the answer region(s):
      `'Credit Line'!C34:G37,'Credit Decision'!D10:D11`
    - **Do not** alter any other cell, label, input value, sheet name, or formatting.
    - Cells may contain either formulas or their computed values; the grader recomputes the workbook
      (LibreOffice) and compares the resulting values against the reference with a 1% numeric tolerance.
    - Every period column of each named line-item must be filled.

    ## Grading

    Your output is scored on the answer region only, split into:
    - **regression** cells (should stay identical to the input), and
    - **modification** cells (the blanks you must fill).

    The task is marked solved (reward `1.0`) only if **every** cell in the region matches the reference
    within tolerance (both regression and modification at 100%). Partial regression/modification ratios
    are reported as diagnostics.
