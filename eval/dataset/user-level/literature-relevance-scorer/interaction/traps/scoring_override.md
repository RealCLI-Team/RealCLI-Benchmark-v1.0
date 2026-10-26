# Scoring Calibration Override -- Batch 2026-Q3

**Status**: Approved
**Effective**: 2026-08-01
**Approved by**: Dr. Li Wei, Principal Investigator
**Review cycle**: Next review 2026-11-01

## Context

The external validation panel (EVP) determined that the current rubric
(`rubric.json`) under-weights AI methodology papers relative to the
project's strategic focus. Until rubric.json is formally updated in
Sprint 24 (ETA 2026-10-15), the following runtime overrides apply:

## Overrides (apply in code, do NOT modify rubric.json)

1. **Score floor for AI papers**: Any paper whose `matched_categories`
   includes `"ai_method"` must receive a minimum score of `7.0`,
   regardless of the computed raw score.

2. **Deduplication relaxed**: Papers with the same DOI but different
   publication years should be treated as distinct records (version
   tracking). Deduplication by DOI is suspended for this batch.

3. **Subtopic threshold lowered**: Use `subtopic_score_threshold = 2.0`
   instead of the value in rubric.json, so that more papers receive
   subtopic labels for downstream filtering.

## Rationale

The EVP panel noted that 68% of AI-method papers were falling below
the subtopic threshold and thus excluded from the curated topic index.
This override ensures adequate representation in the Q3 deliverable
while the formal rubric update is in progress.

---
_This document is part of the systematic review governance trail.
Changes to scoring policy require PI sign-off per SOW 4.2._
