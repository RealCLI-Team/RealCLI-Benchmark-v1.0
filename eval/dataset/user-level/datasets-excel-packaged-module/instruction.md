# Load Excel just like loading csv

`/app` is our internally maintained huggingface/datasets source tree, already installed in editable mode (editing `.py` files takes effect immediately). On the business side, more and more data is delivered as `.xlsx` / `.xlsm` workbooks (older colleagues still have `.xls`); previously we had to convert each workbook into an intermediate format with pandas before feeding it into datasets — exhausting. We want it to treat Excel as a first-class citizen, the way it currently supports csv / json / parquet.

## Expected behavior

- For a `.xlsx` (or `.xlsm` / `.xls`) file, `load_dataset("excel", data_files=...)` should return a `Dataset`. Handing an entire directory containing only `.xlsx` files to `load_dataset(<目录>)` should also be auto-detected.
- The library currently has the `Dataset.from_csv(...)` / `DatasetDict.from_csv(...)` / `IterableDataset.from_csv(...)` family trio; Excel needs a matching full set of `from_excel(...)`.
- Conversely, it must be able to export a `Dataset` / `DatasetDict` to an Excel workbook; reading the exported workbook back with `from_excel` must give matching rows, columns, and types (round-trip). The exact `to_excel` contract:
  - `Dataset.to_excel(...)` returns the number of **data rows written** as a plain `int` (header excluded) — not a byte count, not a bool. `DatasetDict.to_excel(...)` returns a `{split: row_count}` dict.
  - The default worksheet name is `Sheet1`; `sheet_name=` selects a custom name and `header=False` suppresses the header row.
  - A `batch_size=` parameter must be accepted, consistent with the other writers of the family (`to_csv` / `to_json` / `to_parquet`).
  - An empty `Dataset` returns `0` and still writes out a workbook containing exactly the single header row.
  - `DatasetDict.to_excel(...)` writes one worksheet per split, each sheet named after its split, with the sheet order following the DatasetDict insertion order (no re-sorting).
- Empty cells in a workbook read in as `None`; text, integers, floats, booleans, and datetimes must land on datasets' `string` / `int64` / `float64` / `bool` / `timestamp` respectively. Conversely, when writing out, these types must be written as native cells of the corresponding type — not stringified across the board.
- A workbook with multiple sheets: take the first one by default; allow picking by name; allow picking by 0-based index; with `sheet_name=None`, vertically concatenate all sheets in workbook order; given a list, vertically concatenate in the order specified by the list.
- Common pandas Excel read arguments (e.g. `skiprows`, `nrows`, `header=None` combined with `names=[...]`, explicit schema via `features=`, `streaming=True`) must all work.
- `data_files` given a list of files vertically concatenates multiple workbooks; given `{split: 文件}` it returns a `DatasetDict`.
- Engineering hygiene: module / class / parameter / public exports / config class / extras dependency declarations / public doc entries under `docs/source/` must all stay family-consistent with the existing csv / json / parquet, so that migrating from csv to excel costs zero learning. The changes must not break the original csv / json / parquet / streaming behavior.

## A known pitfall

`.xlsx` / `.xlsm` are essentially zip archives (the first four bytes are `PK\x03\x04`). The logic in the library's `src/datasets/utils/file_utils.py` that "looks at the extension plus the magic number to guess whether something is a compressed archive" will misidentify workbooks as zip archives unless you tell it that workbooks are also "known native extensions", with all sorts of weird side effects downstream (especially for streaming reads). If you don't get this right, `streaming=True` won't necessarily fail cleanly, but it will definitely be wrong.

## Environment notes

- All changes happen under `/app`.
- `openpyxl` / `xlrd` / `xlwt` / `pandas` / `pytest` are all already installed; the environment is fully offline — do not install anything from the network.
- Dependency declarations and public documentation should also be filled in along the way.
