import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { basename, dirname, extname, join, resolve } from 'node:path';

const htmlPath = '/volume/pt-coder/users/yke/Frontend-Verif-System/scripts/standalone_verification/worldcoder_official/authored_rubrics/_runs/fw_uncovered_authored/deepfix/P2616_elevator_shaft_vertical_circulation_plan/workspace/index.html';
const BROWSER_ARGS = ['--no-sandbox','--disable-gpu-sandbox','--enable-webgl','--ignore-gpu-blocklist'];

const MIME = {'.html':'text/html; charset=utf-8','.js':'application/javascript','.mjs':'application/javascript','.css':'text/css','.json':'application/json'};

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
console.log('t=1500 init:', JSON.stringify(await page.evaluate(() => ({cf: __3D_STATE__.currentFloor, cabY: __3D_STATE__.cabY, cabSt: __3D_STATE__.cabStatus, dt: __3D_STATE__.distanceTraveled, trips: __3D_STATE__.tripsCompleted}))));

// Simulate the sequence
async function clickButton(id) { await page.evaluate((i) => document.getElementById(i).click(), id); }

await clickButton('addPax');
await page.waitForTimeout(500);
console.log('T2 after addPax:', JSON.stringify(await page.evaluate(() => ({pc: __3D_STATE__.passengerCount, hs: __3D_STATE__.historyStackLength}))));
await clickButton('addPax');
await page.waitForTimeout(500);
console.log('T3:', JSON.stringify(await page.evaluate(() => ({pc: __3D_STATE__.passengerCount, hs: __3D_STATE__.historyStackLength}))));
await clickButton('remPax');
await page.waitForTimeout(500);
console.log('T4:', JSON.stringify(await page.evaluate(() => ({pc: __3D_STATE__.passengerCount, hs: __3D_STATE__.historyStackLength}))));
await clickButton('undoBtn');
await page.waitForTimeout(500);
console.log('T5 (undo):', JSON.stringify(await page.evaluate(() => ({pc: __3D_STATE__.passengerCount, hs: __3D_STATE__.historyStackLength, rs: __3D_STATE__.redoStackLength}))));
await clickButton('redoBtn');
await page.waitForTimeout(500);
console.log('T6 (redo):', JSON.stringify(await page.evaluate(() => ({pc: __3D_STATE__.passengerCount, hs: __3D_STATE__.historyStackLength, rs: __3D_STATE__.redoStackLength}))));

// T7: keyboard '3'
await page.keyboard.press('3');
await page.waitForTimeout(500);
console.log('T7 after key 3 (500ms):', JSON.stringify(await page.evaluate(() => ({cf: __3D_STATE__.currentFloor, cabY: __3D_STATE__.cabY, cabSt: __3D_STATE__.cabStatus, dt: __3D_STATE__.distanceTraveled, trips: __3D_STATE__.tripsCompleted, la: __3D_STATE__.lastAction}))));

// T8: sample over increasing waits
for (const t of [1000, 2000, 3000, 4000, 5000, 8000, 12000]) {
  const start = Date.now();
  await page.waitForTimeout(t - (t === 1000 ? 500 : (t - [1000,2000,3000,4000,5000,8000,12000][([1000,2000,3000,4000,5000,8000,12000].indexOf(t))-1])));
  const s = await page.evaluate(() => ({cf: __3D_STATE__.currentFloor, cabY: __3D_STATE__.cabY, cabSt: __3D_STATE__.cabStatus, dt: __3D_STATE__.distanceTraveled, trips: __3D_STATE__.tripsCompleted, la: __3D_STATE__.lastAction, cwY: __3D_STATE__.counterweightY}));
  console.log('T8 t='+t+':', JSON.stringify(s));
}

await browser.close();
server.close();
