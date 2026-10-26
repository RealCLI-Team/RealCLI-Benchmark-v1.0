# aia-xt1-dl-kaggle 原始数据说明与清洗规则

`aia-xt1-dl-kaggle.zip` 内含三个原始 CSV，均来自不同上游导出，存在编码、
格式和质量问题。请按下列规则清洗，并把结果写入 `outputs/`。

## 归档内容

| 文件 | 主键 | 说明 |
|------|------|------|
| `customers.csv` | `customer_id` | 客户主数据，**以 Latin-1 编码导出**，含重音字符（如 `José`、`Núñez`）。 |
| `products.csv` | `product_id` | 商品主数据（UTF-8）。 |
| `transactions.csv` | `transaction_id` | 交易流水（UTF-8），`amount` 可能带 `$` 和千分位逗号。 |

## 字段标准化规则

- 所有清洗后输出文件统一使用 **UTF-8** 编码，逗号分隔，带表头。
- `region`：统一为首字母大写英文（`North` / `South` / `East` / `West`）。
- `signup_date` 与 `txn_date`：统一规整为 `YYYY-MM-DD`。支持的输入形态包括
  `YYYY-MM-DD`、`YYYY/MM/DD`、`DD/MM/YYYY`。无法解析的日期视为异常。
- `amount`：去掉货币符号 `$` 与千分位逗号后，规整为两位小数的纯数字字符串
  （如 `"$1,234.56"` → `1234.56`，`250` → `250.00`）。
- `unit_price`：保留两位小数。

## 剔除规则（写入 `rejected_records.csv`，不得丢弃）

每条被剔除的记录都要给出一个 `reason`，取值来自下列固定词表：

- `missing_name`：`name` / `product_name` 为空。
- `missing_region`：客户 `region` 为空。
- `invalid_date`：日期无法解析为合法日期。
- `negative_price`：`unit_price` 为负。
- `invalid_amount`：交易 `amount` 为空或无法解析为数字。
- `unknown_customer`：交易引用的 `customer_id` 不在清洗后的客户表中。
- `unknown_product`：交易引用的 `product_id` 不在清洗后的商品表中。

一行可能命中多个问题，按上面列出的顺序取第一个命中的原因即可。
被编码转换失败的行同样写入 `rejected_records.csv`。

## 关联与聚合

用清洗后的三张表做轻量关联：
`transactions` 关联 `customers`（`customer_id`）再关联 `products`（`product_id`），
按 `category`、`region` 分组，统计交易笔数与金额合计，写入
`outputs/aggregated_metrics.csv`。
