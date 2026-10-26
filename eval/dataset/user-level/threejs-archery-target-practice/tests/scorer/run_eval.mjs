#!/usr/bin/env node
// Thin driver: serve one artifact + GLB assets, run the OFFICIAL evaluatePublicPage.
// The scorer (stateprobe/*.mjs, metrics.mjs) is vendored unchanged; this file is glue only.
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { basename, dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { evaluatePublicPage } from './stateprobe/evaluate.mjs';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const BROWSER_ARGS = ['--no-sandbox','--disable-gpu-sandbox','--enable-webgl','--ignore-gpu-blocklist'];
const MIME = { '.html':'text/html; charset=utf-8','.js':'application/javascript','.mjs':'application/javascript',
  '.css':'text/css','.json':'application/json','.png':'image/png','.jpg':'image/jpeg','.glb':'model/gltf-binary',
  '.gltf':'model/gltf+json','.bin':'application/octet-stream','.wasm':'application/wasm' };
// Offline scoring sandbox: the importmap points at LOCAL paths, not a CDN. The
// real three@0.170.0 build lives in --assets-dir (three.module.js, GLTFLoader.js,
// BufferGeometryUtils.js) and is served for ANY request path by the basename
// fallback below — this is the "harness mounts vendor/ at scoring time" mount.
const IMPORTMAP = '<script type="importmap">{"imports":{"three":"/vendor/three.module.js","three/addons/":"/vendor/"}}</script>';

function parseArgs(argv) {
  const o = { html: null, rubric: null, assets: null, output: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i], take = () => argv[++i];
    if (a === '--html-path') o.html = resolve(take());
    else if (a === '--rubric-path') o.rubric = resolve(take());
    else if (a === '--assets-dir') o.assets = resolve(take());
    else if (a === '--output') o.output = resolve(take());
  }
  if (!o.html || !o.rubric) throw new Error('--html-path and --rubric-path are required');
  return o;
}

function startServer(htmlPath, assetsDir) {
  return new Promise(res => {
    const server = createServer((req, r) => {
      try {
        const clean = decodeURIComponent((req.url || '/').split('?')[0]);
        if (clean === '/' || clean === '/index.html') {
          let html = readFileSync(htmlPath, 'utf8');
          if (!html.includes('type="importmap"')) html = html.replace(/<head([^>]*)>/i, `<head$1>${IMPORTMAP}`);
          r.writeHead(200, { 'Content-Type': MIME['.html'], 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store' });
          r.end(html); return;
        }
        const name = basename(clean);
        const htmlDir = dirname(htmlPath);
        // Path-preserving candidate FIRST: serve the artifact's own subdirectory
        // files verbatim (e.g. `vendor/three.min.js`, `libs/OrbitControls.js`).
        // Resolved under htmlDir with a traversal guard so a crafted `../` can
        // never escape the workspace. Without this, self-hosted JS/asset deps in
        // subdirs 404 (THREE never loads -> blank canvas -> false CHECK_FAIL).
        const relResolved = resolve(htmlDir, '.' + (clean.startsWith('/') ? clean : '/' + clean));
        const inWorkspace = relResolved === htmlDir || relResolved.startsWith(htmlDir + '/');
        // Asset resolution order:
        //   0. the artifact's own path-preserved file (subdir-aware, guarded)
        //   1. explicit --assets-dir (GLB pool / override) — preserves prior behavior
        //   2. basename-flattened workspace assets/ + root fallbacks (legacy GLB layout)
        const candidates = [
          inWorkspace ? relResolved : null,
          assetsDir ? join(assetsDir, name) : null,
          join(htmlDir, 'assets', name),
          join(htmlDir, name),
        ].filter(Boolean);
        for (const candidate of candidates) {
          if (existsSync(candidate) && statSync(candidate).isFile()) {
            r.writeHead(200, { 'Content-Type': MIME[extname(name).toLowerCase()] || 'application/octet-stream', 'Access-Control-Allow-Origin': '*' });
            r.end(readFileSync(candidate)); return;
          }
        }
        r.writeHead(404); r.end('Not Found');
      } catch (e) { r.writeHead(400); r.end(String(e.message)); }
    });
    server.listen(0, '127.0.0.1', () => res({ server, port: server.address().port }));
  });
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const rubric = JSON.parse(readFileSync(opts.rubric, 'utf8'));
  const { server, port } = await startServer(opts.html, opts.assets);
  let browser = null, result;
  try {
    browser = await chromium.launch({ headless: true, args: BROWSER_ARGS });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await ctx.newPage(); page.setDefaultTimeout(30000);
    await page.goto(`http://127.0.0.1:${port}/index.html`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(1500);
    result = await evaluatePublicPage(page, rubric);
  } catch (e) {
    result = { mode: 'RUNTIME_CRASH', error: e.message, aCov: 0, sCov: 0, tCov: 0, vCov: 0 };
  } finally {
    await browser?.close().catch(() => {});
    server.close();
  }
  const text = JSON.stringify(result, null, 2);
  if (opts.output) { const { writeFileSync } = await import('node:fs'); writeFileSync(opts.output, text + '\n'); }
  console.log(text);
}
main().catch(e => { console.error(JSON.stringify({ mode: 'RUNTIME_CRASH', error: e.message, aCov:0,sCov:0,tCov:0,vCov:0 })); process.exit(1); });
