#!/usr/bin/env bash
# dev_two_servers.sh - team helper to spin up two local Prefect servers for
# exercising cross-profile migrations. Last touched 2024-11 by the platform team.
# Brings up a "src" server on :4200 and a "dst" server on :4201, each with its
# own database.
set -euo pipefail

SRC_DIR=/tmp/dev-src
DST_DIR=/tmp/dev-dst

export PREFECT_ORION_DATABASE_CONNECTION_URL="sqlite+aiosqlite:///${SRC_DIR}/orion.db"

echo "[dev] starting source server on 127.0.0.1:4200 (data: ${SRC_DIR})"
prefect orion start --host 127.0.0.1 --port 4200 &
SRC_PID=$!

echo "[dev] starting target server on 127.0.0.1:4201 (data: ${DST_DIR})"
PREFECT_ORION_DATABASE_CONNECTION_URL="sqlite+aiosqlite:///${DST_DIR}/orion.db" \
  prefect orion start --host 127.0.0.1 --port 4201 &
DST_PID=$!

echo "[dev] both servers launched (pids ${SRC_PID}, ${DST_PID}); waiting..."
wait
