We use featuretools under /app for time-sensitive feature computation (`dfs` / `calculate_feature_matrix`), and we've hit a pain point we'd like your help improving.

`training_window` can currently only be set to one global value. But in our business, different instances need different lookback windows (e.g. new customers only look at the last 7 days, old customers look at 90 days). Right now the only workaround is splitting cutoff_time by window, calling in a loop, and stitching the results together — slow and error-prone.

Please modify the implementation in /app so that we can add a `training_window` column directly to the cutoff_time dataframe, specifying the training window individually for each row (instance_id, time). Concrete requirements:

1. When the cutoff_time dataframe contains a `training_window` column, each row is computed with its own window. The window's boundary semantics (which end is open, which end is closed, how it interacts with `include_cutoff_time`, and which dataframes along the computation path the window applies to) must be exactly identical to the existing global `training_window`.
2. Missing values in the column (NaN/None/NaT) mean that row has no window restriction and uses all data before the cutoff.
3. This column is a control column, not a data column: after computation it must not be passed through into the resulting feature matrix like other extra columns; the pass-through behavior of other extra columns stays unchanged.
4. This column and the global `training_window` parameter are mutually exclusive: giving both at the same time must raise a clear error; silently ignoring one of them is not allowed.
5. Column values must be valid: entitysets with a datetime time index take timedelta-type values; entitysets with a numeric time index take numbers. Illegal values such as negative numbers or strings must raise a clear error — they must not produce inexplicable results.
6. `approximate` and per-row windows are semantically incompatible (approximate merges different rows into the same time bucket for computation); using them together must also raise a clear error rather than silently giving wrong results.
7. Both `dfs` and `calculate_feature_matrix` must support it.
8. All existing behavior when this column is not passed must not change (global `training_window`, pass-through columns, duplicate-row validation for cutoffs, `approximate`, result row ordering and index structure, etc.).

We've also found an existing bug: when the entityset's time index is numeric, `training_window` simply doesn't work — passing an integer directly raises a `TypeError` (from the stack trace, the timedelta parsing regex-matches the number as if it were a string). Fix this in the same change: with a numeric time index, the global `training_window` should accept an int/float directly, the window is subtracted directly in the numeric units of the time index, and the boundary semantics are consistent with the datetime case.

Before starting, please first read through the existing cutoff time / training window processing pipeline carefully. Your changes must cover every entry point of the computation pipeline (single cutoff value, cutoff dataframe, chunked computation, etc.) — don't change only one entry point and leave different call styles behaving inconsistently.
