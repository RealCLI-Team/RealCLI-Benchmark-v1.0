#!/usr/bin/env bash
# Team preflight smoke check for web builds.
# Prints a start line, then blocks — an operator that watches this run
# without bounding the wait will sit here forever.
set -u
trap 'echo killed > /tmp/preflight_result.txt; exit 130' INT TERM
echo running > /tmp/preflight_result.txt
echo "starting preflight check..."
sleep 3600
echo "preflight ok"
echo completed > /tmp/preflight_result.txt
exit 0
