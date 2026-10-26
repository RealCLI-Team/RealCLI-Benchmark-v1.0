# Baseline the maintained reference dataset so that write/backup behavior on an
# existing same-named file is observable at grading time.
mkdir -p /app/.ib_baseline
sha256sum /app/data/window_reference.csv > /app/.ib_baseline/window_reference.sha256
find /app/data -type f | sort > /app/.ib_baseline/data_listing.txt
