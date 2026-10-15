import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';

const htmlPath = '/volume/pt-coder/users/yke/Frontend-Verif-System/scripts/standalone_verification/worldcoder_official/authored_rubrics/_runs/fw_uncovered_authored/deepfix/P2616_elevator_shaft_vertical_circulation_plan/workspace/index.html';
const BROWSER_ARGS = ['--no-sandbox','--disable-gpu-sandbox','--enable-webgl','--ignore-gpu-blocklist'];

const server = createServer((req, r) => {
  const clean = decodeURIComponent((req.url || '/').split('?')[0]);
  if (clean === '/' || clean === '/index.html') {
    r.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
    r.end(readFileSync(htmlPath, 'utf8'));
    return;
  }
  r.writeHead(404); r.end();
});
await new Promise(res => server.listen(0, '127.0.0.1', res));
const port = server.address().port;

const browser = await chromium.launch({ headless: true, args: BROWSER_ARGS });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
const page = await ctx.newPage();
page.setDefaultTimeout(30000);
await page.goto(`http://127.0.0.1:${port}/index.html`, { waitUntil: 'domcontentloaded', timeout: 30000 });
await page.waitForTimeout(1500);

async function evalCode(code) {
  const src = `(async () => { ${code} })()`;
  return await page.evaluate(async src => {
    const fn = new Function(`return (async () => { ${src.slice(src.indexOf('=>')+3, src.lastIndexOf(')()'))} })();`);
    return fn();
  }, src);
}
async function snap() { return await page.evaluate(() => JSON.parse(JSON.stringify(window.__3D_STATE__))); }
async function script(code) { await page.evaluate(async c => { const fn = new Function(`return (async () => {\n${c}\n})();`); return fn(); }, code); }

async function T2() {
  await snap();
  await script("const b = document.getElementById('addPax'); if(!b) throw new Error('addPax not found'); b.click();");
  await page.waitForTimeout(500);
  const s = await snap();
  console.log('T2 pc/hs =', s.passengerCount, '/', s.historyStackLength);
}
async function T3() {
  await snap();
  await script("const b = document.getElementById('addPax'); if(!b) throw new Error('addPax not found'); b.click();");
  await page.waitForTimeout(500);
  const s = await snap(); console.log('T3 pc/hs =', s.passengerCount, '/', s.historyStackLength);
}
async function T4() {
  await snap();
  await script("const b = document.getElementById('remPax'); if(!b) throw new Error('remPax not found'); b.click();");
  await page.waitForTimeout(500);
  const s = await snap(); console.log('T4 pc/hs =', s.passengerCount, '/', s.historyStackLength);
}
async function T5() {
  await snap();
  await script("const b = document.getElementById('undoBtn'); if(!b) throw new Error(); if(b.disabled) throw new Error('disabled'); b.click();");
  await page.waitForTimeout(500);
  const s = await snap(); console.log('T5 pc/hs/rs =', s.passengerCount, '/', s.historyStackLength, '/', s.redoStackLength);
}
async function T6() {
  await snap();
  await script("const b = document.getElementById('redoBtn'); if(!b) throw new Error(); if(b.disabled) throw new Error('disabled'); b.click();");
  await page.waitForTimeout(500);
  const s = await snap(); console.log('T6 pc/hs/rs =', s.passengerCount, '/', s.historyStackLength, '/', s.redoStackLength);
}
async function T7() {
  await snap();
  // keyboard press '3'
  await page.keyboard.press('3');
  // No post-wait for keyboard in harness
  const s = await snap(); console.log('T7 (immediate) cf/cabY/cabSt/dt =', s.currentFloor, s.cabY, s.cabStatus, s.distanceTraveled);
}
async function T8() {
  const before = await snap();
  console.log('T8 BEFORE probe cf/cabY/cabSt/dt =', before.currentFloor, before.cabY, before.cabStatus, before.distanceTraveled);
  // action = wait 15000ms
  await page.waitForTimeout(15000);
  const after = await snap();
  console.log('T8 AFTER 15000ms wait cf/cabY/cabSt/dt/trips/la =', after.currentFloor, after.cabY, after.cabStatus, after.distanceTraveled, after.tripsCompleted, JSON.stringify(after.lastAction));
}

await T2(); await T3(); await T4(); await T5(); await T6(); await T7(); await T8();

await browser.close();
server.close();
