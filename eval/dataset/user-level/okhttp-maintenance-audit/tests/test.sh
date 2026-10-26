#!/bin/bash
set -euo pipefail

# CliBench: collect interaction evidence on every exit path, after the
# original verifier has already written reward. Never changes the verdict.

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

trap 'if [ -f /tests/collect_evidence.sh ]; then bash /tests/collect_evidence.sh || true; fi' EXIT

mkdir -p /logs/verifier

# The golden expected report is not shipped in the agent workspace (answer-leak
# guard). Inject the verifier's own copy into the unpacked project so that the
# repo's MaintenanceAuditorTest fixture assertion runs during `mvn test`.
FIXTURE=/app/okhttp-real-repo/tools/maintenance-audit/src/test/resources/expected-maintenance-report.json
if [ -d /app/okhttp-real-repo/tools/maintenance-audit ]; then
  mkdir -p "$(dirname "$FIXTURE")"
  cp /tests/expected-maintenance-report.json "$FIXTURE"
fi

if pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi
