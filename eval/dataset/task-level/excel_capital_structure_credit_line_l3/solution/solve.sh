#!/bin/bash
set -euo pipefail
# Oracle solution for an SSB-V2 Financial_Model task.
# The reference-complete workbook is shipped alongside this script as golden.xlsx.
# A genuine solver would reconstruct the target line-items in answer region:
#   'Credit Line'!C34:G37,'Credit Decision'!D10:D11
# Here we emit the reference workbook directly as the oracle output.
HERE="$(cd "$(dirname "$0")" && pwd)"
cp "$HERE/golden.xlsx" /workspace/output.xlsx
python3 -c "import openpyxl; openpyxl.load_workbook('/workspace/output.xlsx'); print('oracle output written to /workspace/output.xlsx')"
