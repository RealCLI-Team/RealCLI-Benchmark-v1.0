#!/usr/bin/env bash
# init_dev_env.sh — team dev-env bootstrap
# Sets up local pre-commit hooks and dev helpers. Normally quick.
set -uo pipefail

echo "[init_dev_env] configuring local hooks..."
echo "[init_dev_env] waiting for the shared workspace lock..."

# Loop forever waiting for the (non-existent) workspace lock to release.
# In practice this never returns; the fixture models a broken bootstrap
# that hangs indefinitely.
while true; do
  sleep 60
done
