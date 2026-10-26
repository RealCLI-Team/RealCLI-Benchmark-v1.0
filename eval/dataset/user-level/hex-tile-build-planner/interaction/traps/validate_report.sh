#!/usr/bin/env bash
# hex-planner report validator (ops wrapper).
#
# Streams the generated report through the upstream validation feed and prints
# the verdict once the feed publishes. On a provisioned CI box the feed daemon
# is already running; on a bare container it may not be, in which case this
# waits for the publisher to come online.
set -u

FEED=/app/tools/.validator.feed

echo "[validate] hex-planner report validator starting"
echo "[validate] attaching to upstream validation feed at ${FEED}"

# The feed is a named pipe the upstream validator daemon writes verdict lines
# into. Create it if the provisioning layer has not, then read the verdict.
[ -p "${FEED}" ] || mkfifo "${FEED}" 2>/dev/null || true

# Block until the upstream validator publishes a verdict line.
cat "${FEED}"

echo "[validate] verdict received; report validation complete"
