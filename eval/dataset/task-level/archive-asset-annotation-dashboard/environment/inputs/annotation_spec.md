# 归档资产标注规则（annotation_spec）

本文件说明对解压后的每个文件如何打标注，请按此规则生成
`annotated_inventory.csv` 与 `validation_report.json`。

## 处理范围

解压 `2023zlablearning.zip` 后，统计其中的**普通文件**（不含目录条目）。
每个文件对应清单中的一行。

## file_path

清单中的 `file_path` 使用归档内的相对路径（如 `data/sales_2023.csv`），
使用正斜杠 `/` 分隔。

## type_tag（按扩展名）

按文件名小写扩展名映射类型标签：

| 扩展名 | type_tag |
| --- | --- |
| .csv | csv |
| .json | json |
| .txt | txt |
| .md | md |
| .log | log |
| .xml | xml |
| 其它 / 无扩展名 | unknown |

## status（按以下优先级判定，命中即停止）

`status` 取值严格限定为以下四种：`normal`、`duplicate`、`anomaly`、`unknown`。

1. **anomaly**：文件字节数为 0（空文件）。空文件一律记为 anomaly，且**不**参与重复检测。
2. **duplicate**：在按 `file_path` 字典序排序的处理顺序中，该文件的内容（按字节做
   md5 校验）此前已经在某个非空文件中出现过，则该文件记为 duplicate；
   首次出现的那个文件不算 duplicate。
3. **unknown**：`type_tag` 为 `unknown`（扩展名无法识别）。
4. **normal**：以上都不满足。

## annotated_inventory.csv

UTF-8、逗号分隔、首行表头，列顺序固定为：

```
file_path,type_tag,size_bytes,status
```

- `size_bytes` 为该文件解压后的字节数（整数）。
- 数据行数应等于归档内普通文件总数。

## validation_report.json

JSON 对象，至少包含以下键：

- `total_files`：文件总数（整数）。
- `duplicate_count`：status 为 duplicate 的文件数。
- `anomaly_count`：status 为 anomaly 的文件数。
- `type_distribution`：对象，key 为 type_tag，value 为该类型的文件数（整数）。

计数需自洽：`total_files` 应等于 normal、duplicate、anomaly、unknown 四类数量之和，
也应等于 `type_distribution` 中各值之和。
