# Personal notes on the ARS batch driver task

These are Sam's scratch notes from earlier in the week. They're not part of
the task deliverable — please leave this file alone.

- Reminder: cases.tsv has grown to about ten rows; check with Priya before
  adding anything else so we do not clobber the tolerance calibration.
- The weibull override lives in overrides.tsv now; do not fold it back into
  cases.tsv or the next diff review will get noisy.
- Ellis wanted a follow-up on whether we ever cache the true moments; we
  agreed to punt on that until after the sampler ships.
- The dup-and-space case_id row is intentional; do not "clean it up".
