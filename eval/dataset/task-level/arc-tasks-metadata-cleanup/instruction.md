# ARC Task Metadata Cleanup

Hi, I downloaded the abstraction-and-reasoning-challenge archive from Kaggle and put it at `inputs/data/abstraction-and-reasoning-challenge.zip`. Inside there are three subdirectories — training/, evaluation/, and test/ — each containing a bunch of ARC task JSON files.

I need it for a group meeting, so please help me with a few things:

1. Extract the archive and scan all JSON task files across the three subsets; pull out the basic metadata for each task (number of train/test examples, maximum grid size, number of colors, etc.).
2. Filter out corrupted or non-compliant samples (broken JSON, missing train/test, grids that aren't two-dimensional integers, out-of-range colors, etc.), and record the reason each one was dropped.
3. Aggregate the statistics by subset.
4. Produce an HTML report I can double-click to open, so I can project it at the group meeting.

Please put the final artifacts under `outputs/`:

- `outputs/tasks_clean.csv`: the cleaned task table
- `outputs/cleaning_report.json`: statistics about the cleaning process
- `outputs/subset_summary.csv`: statistics aggregated by subset
- `outputs/report.html`: an offline HTML report (embedded tables + at least one statistical chart; don't reference any external web resources)

The strict conventions for the specific fields and columns are in `inputs/docs/output_spec.md` — don't change column names or their order, or my downstream scripts will break.

Note: the whole process must run offline; do not access the network.
