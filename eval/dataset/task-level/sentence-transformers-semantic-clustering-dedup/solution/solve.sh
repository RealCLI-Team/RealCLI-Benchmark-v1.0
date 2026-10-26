#!/usr/bin/env bash
set -euo pipefail
cd /app
# Idempotent: skip if the toolkit + the community_detection determinism/API-object refactor are
# already in place. We fingerprint both the new clustering module AND the specific
# `return_result` keyword added to the modified `community_detection`, so a partial replay of the
# original patch on top of a pristine repo still triggers a re-apply.
if [ -f /app/sentence_transformers/util/clustering.py ] \
    && grep -q "CommunityDetectionResult" /app/sentence_transformers/util/clustering.py \
    && grep -q "return_result" /app/sentence_transformers/util/retrieval.py \
    && grep -q "kmeans_clustering" /app/sentence_transformers/util/__init__.py; then
  exit 0
fi
patch -p1 < /solution/patch.diff
