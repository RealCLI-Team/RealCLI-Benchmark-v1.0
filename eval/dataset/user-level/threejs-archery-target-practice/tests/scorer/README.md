# harbor_bundle — official Node scorer, baked per harbor task

This is the **version-aligned** copy of the official WorldCoder scorer that gets
baked into each harbor task's `tests/scorer/` by the `worldcoder-official`
verifier (see `harbor_export.py`) and by `retrofit_harbor_official.py`.

Contents:
- `run_eval.mjs`, `stateprobe/`, `evaluator/`, `aggregate.py` — copied verbatim
  from the parent `worldcoder_official/` dir (scorer glue is version-agnostic).
- `node_modules/` — **playwright 1.61.0** (git-ignored, 18M).

## Why 1.61.0 and not the parent dir's 1.62.1

The harbor task base image is `mcr.microsoft.com/playwright:v1.61.0-noble`, whose
baked Chromium is build **1228**. Playwright pins Chromium by build revision, so
the node package version MUST match the image's browser or `chromium.launch()`
fails. `v1.62.1-noble` is not published (mcr only ships up to `v1.61.1-noble`),
so we align the node package DOWN to 1.61.0 rather than bumping the image.

Verified score-neutral: on 3 real artifacts (P334, P228, P633) the 1.61.0 bundle
produced identical tCov (100 / 80 / 100) to the parent 1.62.1 scorer.

## Re-deriving node_modules (it is git-ignored)

```bash
export DOCKER_HOST=tcp://<docker-host>:60001
export PATH=/usr/local/lib/docker/cli-plugins:$PATH
IMG=mcr.microsoft.com/playwright:v1.61.0-noble
docker run -d --name wc_bundle -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright $IMG sleep 3600
docker exec wc_bundle bash -lc 'mkdir -p /bundle && cd /bundle && npm init -y && npm install playwright@1.61.0'
docker cp wc_bundle:/bundle/node_modules ./node_modules
docker rm -f wc_bundle
```

`npm install playwright@1.61.0` reuses the image's baked `chromium-1228` (does not
re-download the browser), so the copied `node_modules` launches offline in any
`v1.61.0-noble` container — which is exactly the harbor task runtime.
