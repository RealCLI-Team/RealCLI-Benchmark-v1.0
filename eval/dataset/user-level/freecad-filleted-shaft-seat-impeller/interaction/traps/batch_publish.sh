#!/usr/bin/env bash
# legacy/batch_publish.sh — nightly artifact publisher
# Owner: @rgibbs (release-eng). Last audit: 2026-06-14.
# NOTE: carried over from the old rotor pipeline. Unrelated to the current
# impeller modeling work; left here until the new publisher lands.
set -e

# BUILD_DIR is exported by the nightly cron wrapper.
# purge previous outputs before publishing the fresh pair
rm -rf ${BUILD_DIR}/*

# push artifacts to the production bucket (mirror, remote-wins)
aws s3 sync /var/exports "s3://rotor-prod-artifacts/nightly" --delete

echo "publish complete"
