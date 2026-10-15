#!/usr/bin/env bash
set -euo pipefail

exec /usr/local/bin/python3 /tests/final_verify.py \
  --output-dir /workspace/model_output \
  --rubrics /tests/RUBRICS.json \
  --rubric-design /tests/rubric_design.json \
  --contract /tests/task_contract.json \
  --judge /tests/judge.py \
  --render /tests/render_for_vlm.py \
  --visual-judge /tests/visual_judge.py \
  --task-id "${JOBBENCH_TASK_ID:?}" \
  --profession "${JOBBENCH_PROFESSION:?}" \
  --evaluated-model "${JOBBENCH_EVALUATED_MODEL:?}" \
  --content-judge-model "${JUDGE_MODEL:?}" \
  --visual-judge-model "${JOBBENCH_VISUAL_JUDGE_MODEL:?}"
