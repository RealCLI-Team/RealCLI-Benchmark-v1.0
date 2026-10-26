#!/usr/bin/env bash
set -euo pipefail


# CliBench maven 离线补齐（2026-09-18）：镜像 /root/.m2 缺 surefire 的
# junit-platform provider 与 platform-launcher，禁网下 mvn test 必挂。
# 这两个 jar 随 tests/ 上传，装进本地仓库。
_m2_repo=/root/.m2/repository
for _f in /tests/_m2/*; do
  _b=$(basename "$_f")
  case "$_b" in
    surefire-junit-platform-*) _d=org/apache/maven/surefire/surefire-junit-platform/3.2.5;;
    surefire-providers-*) _d=org/apache/maven/surefire/surefire-providers/3.2.5;;
    common-java5-*) _d=org/apache/maven/surefire/common-java5/3.2.5;;
    surefire-3.2.5.pom) _d=org/apache/maven/surefire/surefire/3.2.5;;
    maven-parent-*) _d=org/apache/maven/maven-parent/40;;
    apache-29.pom) _d=org/apache/apache/29;;
    junit-bom-*) _d=org/junit/junit-bom/5.10.2;;
    junit-platform-launcher-1.9.3.*) _d=org/junit/platform/junit-platform-launcher/1.9.3;;
    junit-platform-engine-1.9.3.*) _d=org/junit/platform/junit-platform-engine/1.9.3;;
    junit-platform-commons-1.9.3.*) _d=org/junit/platform/junit-platform-commons/1.9.3;;
    opentest4j-*) _d=org/opentest4j/opentest4j/1.2.0;;
    apiguardian-api-*) _d=org/apiguardian/apiguardian-api/1.1.2;;
    junit-platform-launcher-*) _d=org/junit/platform/junit-platform-launcher/1.10.2;;
    *) continue;;
  esac
  mkdir -p "$_m2_repo/$_d" && cp "$_f" "$_m2_repo/$_d/"
done

TESTS_ROOT="${TESTS_ROOT:-/tests}"
LOGS_ROOT="${LOGS_ROOT:-/logs/verifier}"

mkdir -p "$LOGS_ROOT"
trap 'rc=$?; [ -s "$LOGS_ROOT/reward.txt" ] || echo 0 > "$LOGS_ROOT/reward.txt"; if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi; exit $rc' EXIT
set +e
pytest --junitxml "$LOGS_ROOT/junit.xml" "$TESTS_ROOT/test_outputs.py" -rA
PYTEST_STATUS=$?
set -e

python3 "$TESTS_ROOT/score_capabilities.py" \
  "$TESTS_ROOT/test_manifest.json" \
  "$LOGS_ROOT/junit.xml" \
  "$LOGS_ROOT/score.json"

SCORE_JSON="$LOGS_ROOT/score.json" python3 - <<'PY' > "$LOGS_ROOT/reward.txt"
import json
import os
from pathlib import Path
score = json.loads(Path(os.environ["SCORE_JSON"]).read_text())
print(score["overallScore"])
PY

echo "pytest_status=${PYTEST_STATUS}" > "$LOGS_ROOT/status.txt"
exit 0
