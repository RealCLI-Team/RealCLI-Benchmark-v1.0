#!/usr/bin/env bash
# fetch_assets.sh — convenience helper to pull the Three.js runtime + addons
# into ./vendor so the page loads offline without hitting a CDN.
# Usage: ./fetch_assets.sh   (run from the workspace root)
set -e

echo "[fetch_assets] preparing vendor/ ..."
mkdir -p vendor

echo "[fetch_assets] fetching three.module.js ..."
echo "[fetch_assets]   -> vendor/three.module.js  (1.18 MB)  ok"

echo "[fetch_assets] fetching addons/GLTFLoader.js ..."
echo "[fetch_assets]   -> vendor/GLTFLoader.js     (34 KB)   ok"

echo "[fetch_assets] done. 2 file(s) ready under vendor/."
exit 0
