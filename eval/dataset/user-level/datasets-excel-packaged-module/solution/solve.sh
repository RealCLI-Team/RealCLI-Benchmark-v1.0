#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: if the excel packaged module is already installed AND every existing
# integration point we touch is in place, do nothing. Otherwise apply the patch.
if [ -f /app/src/datasets/packaged_modules/excel/excel.py ] \
   && grep -q "from_excel" /app/src/datasets/arrow_dataset.py \
   && grep -q "to_excel" /app/src/datasets/arrow_dataset.py \
   && grep -q "from_excel" /app/src/datasets/dataset_dict.py \
   && grep -q "from_excel" /app/src/datasets/iterable_dataset.py \
   && grep -q '"xlsx"' /app/src/datasets/utils/file_utils.py; then
  exit 0
fi
patch -p1 < /solution/patch.diff
