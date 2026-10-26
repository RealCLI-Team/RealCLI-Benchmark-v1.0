#!/usr/bin/env bash
# Interaction layer post-install: prepare recording tree and canary.
# Idempotent; never asserts, never affects reward.
mkdir -p /app/recordings
mkdir -p /app/recordings/scratch
mkdir -p /root/.interaction
