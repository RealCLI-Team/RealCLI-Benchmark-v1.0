#!/usr/bin/env node
// Lightweight health probe: render one artifact and report whether it exposes a
// runtime window.__3D_STATE__. Reuses the same serve+launch glue as run_eval.mjs
// and the vendored StateProbeProtocol.detect(), but runs NO rubric — it answers
// only "does this scene populate __3D_STATE__ at runtime?" for flywheel ingest.
//
// Usage:   node stateprobe/has3d.mjs --workspace <dir>   [--html-path <index.html>]
// Output:  last stdout line = {"available":bool,"failureMode":str|null}
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { basename, dirname, extname, join, resolve } from 'node:path';
import { StateProbeProtocol } from './probe.mjs';

const BROWSER_ARGS = ['--no-sandbox','--disable-gpu-sandbox','--enable-webgl','--ignore-gpu-blocklist'];
const MIME = { '.html':'text/html; charset=utf-8','.js':'application/javascript','.mjs':'application/javascript',
  '.css':'text/css','.json':'application/json','.png':'image/png','.jpg':'image/jpeg','.glb':'model/gltf-binary',
  '.gltf':'model/gltf+json','.bin':'application/octet-stream','.wasm':'application/wasm' };
const IMPORTMAP = '<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}</script>';

function parseArgs(argv) {
  const o = { html: null, workspace: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i], take = () => argv[++i];
    if (a === '--html-path') o.html = resolve(take());
    else if (a === '--workspace') o.workspace = resolve(take());
  }
  if (!o.html && o.workspace) o.html = join(o.workspace, 'index.html');
  if (!o.html) throw new Error('--workspace or --html-path is required');
  return o;
}

function startServer(htmlPath) {
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
        const candidates = [join(htmlDir, 'assets', name), join(htmlDir, name)];
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
  if (!existsSync(opts.html)) { console.log(JSON.stringify({ available: false, failureMode: 'PROBE_MISSING' })); return; }
  const { server, port } = await startServer(opts.html);
  let browser = null, result;
  try {
    browser = await chromium.launch({ headless: true, args: BROWSER_ARGS });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await ctx.newPage(); page.setDefaultTimeout(30000);
    await page.goto(`http://127.0.0.1:${port}/index.html`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(1500);   // let the scene boot and populate __3D_STATE__
    result = await new StateProbeProtocol(page).detect();
  } catch (e) {
    result = { available: false, failureMode: 'RUNTIME_CRASH', error: e.message };
  } finally {
    await browser?.close().catch(() => {});
    server.close();
  }
  console.log(JSON.stringify(result));
}
main().catch(e => { console.log(JSON.stringify({ available: false, failureMode: 'RUNTIME_CRASH', error: e.message })); process.exit(1); });
