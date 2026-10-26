#!/usr/bin/env bash
set -euo pipefail

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.
trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT

exec /usr/local/bin/python3 /tests/final_verify.py \
  --output-dir /workspace/model_output \
  --rubrics /tests/RUBRICS.json \
  --rubric-design /tests/rubric_design.json \
  --contract /tests/task_contract.json \
  --judge /tests/judge.py \
  --render /tests/render_for_vlm.py \
  --visual-judge /tests/visual_judge.py \
  --task-id "${CLIBENCH_TASK_ID:?}" \
  --profession "${CLIBENCH_PROFESSION:?}" \
  --evaluated-model "${CLIBENCH_EVALUATED_MODEL:?}" \
  --content-judge-model "${JUDGE_MODEL:?}" \
  --visual-judge-model "${CLIBENCH_VISUAL_JUDGE_MODEL:?}"
