import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { basename, dirname, extname, join, resolve } from 'node:path';
const HTML = process.argv[2];
const MIME = { '.html':'text/html; charset=utf-8','.js':'application/javascript','.mjs':'application/javascript'};
function startServer(htmlPath){
  return new Promise(res=>{
    const s = createServer((req,r)=>{
      const clean = decodeURIComponent((req.url||'/').split('?')[0]);
      if (clean==='/'||clean==='/index.html'){
        r.writeHead(200,{'Content-Type':MIME['.html']}); r.end(readFileSync(htmlPath,'utf8')); return;
      }
      const p = resolve(dirname(htmlPath), '.'+clean);
      if (existsSync(p)&&statSync(p).isFile()){
        r.writeHead(200,{'Content-Type':MIME[extname(p).toLowerCase()]||'application/octet-stream'});
        r.end(readFileSync(p)); return;
      }
      r.writeHead(404); r.end();
    });
    s.listen(0,'127.0.0.1',()=>res({s,port:s.address().port}));
  });
}
const {s,port} = await startServer(HTML);
const b = await chromium.launch({headless:true, args:['--no-sandbox','--disable-gpu-sandbox','--enable-webgl','--ignore-gpu-blocklist']});
const ctx = await b.newContext({viewport:{width:1280,height:720}});
const page = await ctx.newPage();
page.on('console', m => console.error('[console]', m.type(), m.text()));
page.on('pageerror', e => console.error('[pageerror]', e.message));
page.on('requestfailed', r => console.error('[reqfail]', r.url(), r.failure()?.errorText));
page.on('response', r => { if (r.status()>=400) console.error('[resp]', r.status(), r.url()); });
await page.goto(`http://127.0.0.1:${port}/index.html`,{waitUntil:'networkidle', timeout:30000}).catch(e=>console.error('goto err:', e.message));
await page.waitForTimeout(2000);
const t = await page.evaluate(() => ({
  hasTHREE: typeof THREE!=='undefined',
  hasOrbit: typeof THREE!=='undefined' && !!THREE.OrbitControls,
  hasState: typeof window.__3D_STATE__==='object' && window.__3D_STATE__!==null,
  stateKeys: window.__3D_STATE__ ? Object.keys(window.__3D_STATE__).slice(0,20) : null,
  bodyLen: document.body.innerHTML.length,
})).catch(e=>({err:e.message}));
console.error('POST:', JSON.stringify(t));
await b.close(); s.close();
