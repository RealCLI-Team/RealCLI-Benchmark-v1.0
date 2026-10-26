I need you to help me process a batch of local materials offline; the topic is "clean this batch of Kaggle-exported data for me". Please complete it directly according to the requirements below; keep output file names, field names, enum values, and key formats verifiable.

I packaged a Kaggle dataset exported from an upstream system into `inputs/aia-xt1-dl-kaggle.zip`. Unzipped, it's three CSVs: customers, products, and transaction logs. Lots of problems — the customers table was exported in Latin-1, so names with accents (like José, Núñez) show up as mojibake on my side; date formats are mixed across three styles; some transaction amounts carry dollar signs and thousands separators; and there are also empty names, negative prices, and transactions referencing nonexistent customers or products.

I'm working on a machine with no internet access, so please write a re-runnable cleaning pipeline to tidy all of this up. The concrete field-standardization rules, the rejection-reason vocabulary, and the column conventions for each output file are all written in `inputs/README.md` and `inputs/output_schema.json` — just follow those two files. I don't want to lose a single row that gets rejected by the rules or fails conversion; they must be fully archived. Also, please do a light join of the three cleaned tables and aggregate transaction stats by product category and customer region.

Finally I want an offline HTML quality report that opens with a double-click, containing the missing-value distribution, grouped aggregation stats, and a quality-score table — don't reference anything from the external web (I can't open it here). Also generate an md5 manifest of all input and output files for future verification.

The deliverables are roughly these:
- cleaned `customers_cleaned.csv`, `transactions_cleaned.csv`, `products_cleaned.csv` under `outputs/`
- the quarantined `outputs/rejected_records.csv` and the joined aggregate `outputs/aggregated_metrics.csv`
- pre/post-cleaning row/column diffs and rejection stats `outputs/dimensions_audit.txt`
- the offline quality report `outputs/quality_report.html` and the checksum manifest `outputs/manifest.md5`
- the whole pipeline wrapped into a re-runnable script `scripts/run_pipeline.sh`
