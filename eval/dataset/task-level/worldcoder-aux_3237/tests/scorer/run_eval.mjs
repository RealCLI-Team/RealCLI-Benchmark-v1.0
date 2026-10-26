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
const IMPORTMAP = '<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}</script>';

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

// Collect chromium page-level failures the scorer can't see from inside the page.
// Attached BEFORE goto so a script-tag pageerror during initial load isn't missed.
// Capped so a runaway artifact can't blow up the verdict payload.
const PAGEERR_CAP = 8, CONSOLEERR_CAP = 8, REQFAIL_CAP = 8, HTTPERR_CAP = 8;
function _attachPageDiagnostics(page) {
  const d = { pageErrors: [], consoleErrors: [], requestFailures: [], httpErrors: [] };
  page.on('pageerror', e => { if (d.pageErrors.length < PAGEERR_CAP) d.pageErrors.push(String(e.message || e)); });
  page.on('console', m => {
    if (m.type() === 'error' && d.consoleErrors.length < CONSOLEERR_CAP) d.consoleErrors.push(m.text());
  });
  page.on('requestfailed', r => {
    if (d.requestFailures.length < REQFAIL_CAP)
      d.requestFailures.push({ url: r.url(), error: r.failure()?.errorText || null });
  });
  page.on('response', r => {
    if (r.status() >= 400 && d.httpErrors.length < HTTPERR_CAP)
      d.httpErrors.push({ url: r.url(), status: r.status() });
  });
  return d;
}
function _synthesizeError(diag) {
  // Deterministic, short single-line summary the pipeline layer can surface.
  if (diag.pageErrors.length) return 'pageerror: ' + diag.pageErrors[0].split('\n')[0].slice(0, 240);
  if (diag.requestFailures.length) {
    const rf = diag.requestFailures[0];
    return 'requestfailed: ' + (rf.error || 'unknown') + ' — ' + rf.url.slice(0, 200);
  }
  if (diag.httpErrors.length) {
    const he = diag.httpErrors[0];
    return 'http_error: ' + he.status + ' ' + he.url.slice(0, 200);
  }
  if (diag.consoleErrors.length) return 'console.error: ' + diag.consoleErrors[0].split('\n')[0].slice(0, 240);
  return null;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const rubric = JSON.parse(readFileSync(opts.rubric, 'utf8'));
  const { server, port } = await startServer(opts.html, opts.assets);
  let browser = null, result, diag = { pageErrors: [], consoleErrors: [], requestFailures: [], httpErrors: [] };
  try {
    browser = await chromium.launch({ headless: true, args: BROWSER_ARGS });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await ctx.newPage(); page.setDefaultTimeout(30000);
    diag = _attachPageDiagnostics(page);
    await page.goto(`http://127.0.0.1:${port}/index.html`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(1500);
    result = await evaluatePublicPage(page, rubric);
  } catch (e) {
    result = { mode: 'RUNTIME_CRASH', error: e.message, aCov: 0, sCov: 0, tCov: 0, vCov: 0 };
  } finally {
    await browser?.close().catch(() => {});
    server.close();
  }
  // Merge chromium-level diagnostics into every result path — including the happy path,
  // so a CHECK_PASS still surfaces console.errors / late 4xx assets a rubric author cares
  // about. `error` field: keep any existing (thrown) message; only fill from diagnostics
  // when it's absent, so the pipeline's "error != null" signal points at the real cause
  // instead of null when L0_probe fails inside evaluatePublicPage.
  result.pageErrors = diag.pageErrors;
  result.consoleErrors = diag.consoleErrors;
  result.requestFailures = diag.requestFailures;
  result.httpErrors = diag.httpErrors;
  if (!result.error) {
    const syn = _synthesizeError(diag);
    if (syn) result.error = syn;
  }
  const text = JSON.stringify(result, null, 2);
  if (opts.output) { const { writeFileSync } = await import('node:fs'); writeFileSync(opts.output, text + '\n'); }
  console.log(text);
}
main().catch(e => { console.error(JSON.stringify({ mode: 'RUNTIME_CRASH', error: e.message, aCov:0,sCov:0,tCov:0,vCov:0 })); process.exit(1); });
