#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../tools/maintenance-audit"
mvn -q -DskipTests package
java -cp target/classes org.terminalbench.realrepo.MaintenanceAuditCli
