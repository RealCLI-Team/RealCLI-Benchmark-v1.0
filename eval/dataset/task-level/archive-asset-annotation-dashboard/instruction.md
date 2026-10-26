# 2023 Z-Lab Learning Archive: Asset Annotation and Health Check

We received an offline archive bundle from last year, `inputs/2023zlablearning.zip`, containing a mix of
study materials and data files (CSV, JSON, logs, documents, and the like). We now want to bring it into the
asset registry, but the archive has quite a few legacy issues — some files are duplicate copies, some are
empty files, and there are strange extensions we have no idea about. I need it health-checked first, with an
auditable inventory produced.

Please help me extract this zip into the working directory, then automatically annotate the type of every
file inside, and flag duplicate files and abnormal files. How exactly to label, what status values exist, and
which columns the CSV needs — I've written all of that up in
`inputs/annotation_spec.md`; just follow that. Use only the Python standard library and common command-line
tools (unzip, md5sum, and the like); the whole environment is offline — don't connect to the network.

I also want a small dashboard `dashboard.html` that can be opened directly in a browser, so the type
distribution and abnormal files are visible at a glance. I've written the dashboard's content points in
`inputs/dashboard_outline.md`; note that it must be offline and self-contained — don't reference anything
from the external web.

Deliverables:

- `annotated_inventory.csv`: the per-file annotated inventory
- `validation_report.json`: summary statistics (totals, duplicates, abnormals, type distribution)
- `dashboard.html`: the offline visualization dashboard
