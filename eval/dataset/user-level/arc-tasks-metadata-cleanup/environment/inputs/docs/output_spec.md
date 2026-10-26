# 输出契约

本文件规定四个交付物的严格字段和格式，脚本下游依赖，请勿改动列名或顺序。

## 1. outputs/tasks_clean.csv

表头必须严格为以下 9 列，顺序不得变动：

```
task_id,subset,n_train,n_test,max_input_h,max_input_w,max_output_h,max_output_w,n_colors
```

- `task_id`：JSON 文件名去掉 `.json` 后缀。
- `subset`：取值仅限 `training` / `evaluation` / `test`。
- `n_train`、`n_test`：非负整数，分别为 train/test 样本数。
- `max_input_h`、`max_input_w`、`max_output_h`、`max_output_w`：该任务所有样本中 input/output 网格的最大高/宽。
- `n_colors`：该任务所有 train+test 样本 input 与 output 网格中出现的不同整数值个数。
- `task_id` 列必须唯一。

## 2. outputs/cleaning_report.json

必须包含以下四个字段：

- `files_scanned`：扫描到的 JSON 文件总数。
- `files_valid`：通过校验并写入 tasks_clean.csv 的文件数。
- `files_dropped`：被丢弃的文件数。
- `dropped_reasons`：对象，键为丢弃原因，值为该原因下的文件数。

且必须满足 `files_scanned == files_valid + files_dropped`。

丢弃规则（任一命中即丢弃）：

1. JSON 解析失败
2. 缺少 `train` 或 `test` 键
3. 任一样本缺少 `input` 或 `output`
4. 网格非二维整型
5. 颜色值不在 0-9 范围

## 3. outputs/subset_summary.csv

至少包含以下 4 列：

```
subset,task_count,avg_n_train,avg_n_colors
```

- 按 `subset` 分组聚合。
- `avg_n_train`、`avg_n_colors` 保留两位小数。

## 4. outputs/report.html

- 单文件、离线可打开，不引用任何外网 CDN / JS / 图片。
- 必须是合法的 HTML 文档，包含 `<html` 起始标签（大小写不敏感）。
- 页面内需包含 `subset_summary` 表格。
- 至少包含一张统计图：`<img>` 内嵌 base64 PNG 或内联 `<svg>` 均可。
