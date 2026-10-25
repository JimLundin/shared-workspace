import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { extname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, webkit } from '../../checks/node_modules/playwright/index.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const evidence = resolve(option('--evidence', '/tmp/engine-stages'));
const assetsRoot = resolve(option('--asset-root', root));
const engineNames = option('--engines', 'chromium,webkit').split(',');
const mode = option('--mode', 'worker');
const types = { chromium, webkit };
const origin = 'https://iroh-engine-stages.invalid';
const browsers = [];
const pages = [];
const stages = [];
const since = Date.now();
let stage = 'setup';
let failure;
const implementation = `
import init, { BrowserEndpoint } from '/pkg/browser_workspace_host.js';
await init();
let endpoint;
globalThis.run = async (method, params) => {
  if (method === 'open') {
    const seed = Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2, '0')).join('');
    endpoint = await BrowserEndpoint.start(seed, async (peer, payload) => payload);
    return endpoint.id();
  }
  if (method === 'address') return endpoint.address();
  if (method === 'request') return endpoint.request(params, 'synthetic engine exchange');
  if (method === 'close') { await endpoint?.close(); endpoint?.free(); endpoint = undefined; }
};
globalThis.onmessage = async ({data}) => {
  try { postMessage({id:data.id,ok:true,result:await run(data.method,data.params)}); }
  catch(error) { postMessage({id:data.id,ok:false,error:String(error?.message ?? error)}); }
};
if (typeof document === 'undefined') postMessage({ready:true});
`;
function observe(name, details = {}) {
  stage = name;
  const item = { stage, elapsed_ms: Date.now() - since, ...details };
  stages.push(item); console.log(JSON.stringify(item));
}
async function call(page, engine, method, params) {
  observe(`${engine}.${method}.begin`);
  const start = Date.now();
  const result = await page.evaluate(({method,params}) => window.invoke(method, params), {method,params});
  observe(`${engine}.${method}.completed`, { duration_ms: Date.now() - start });
  return result;
}
try {
  for (const engine of engineNames) {
    const browser = await types[engine].launch({ headless: true }); browsers.push(browser);
    const context = await browser.newContext();
    await context.route(`${origin}/**`, async route => {
      const path = new URL(route.request().url()).pathname;
      const headers = {'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'};
      if (path === '/') return route.fulfill({contentType:'text/html',headers,body:'<!doctype html><title>Iroh stage check</title>'});
      if (path === '/endpoint.js') return route.fulfill({contentType:'text/javascript',headers,body:implementation});
      const local = resolve(assetsRoot,'.'+path); if (!local.startsWith(assetsRoot)) return route.abort();
      await route.fulfill({headers,body:await readFile(local),contentType:extname(path)==='.wasm'?'application/wasm':'text/javascript'});
    });
    const page = await context.newPage(); pages.push(page);
    await page.goto(origin);
    await page.evaluate(async mode => {
      if (mode === 'main') {
        await import('/endpoint.js'); window.invoke = window.run; return;
      }
      const worker = new Worker('/endpoint.js',{type:'module'});
      let id = 0; const waiting = new Map();
      const ready = new Promise(resolve => {
        worker.onmessage = ({data}) => {
          if (data.ready) { resolve(); return; }
          const pending = waiting.get(data.id); waiting.delete(data.id);
          if (data.ok) pending.resolve(data.result); else pending.reject(new Error(data.error));
        };
      });
      window.invoke = (method,params) => new Promise((resolve,reject) => { waiting.set(++id,{resolve,reject}); worker.postMessage({id,method,params}); });
      await ready;
    }, mode);
    observe(`${engine}.ready`, { version:browser.version(),mode });
  }
  const ids = []; const addresses = [];
  for (let index=0;index<pages.length;index++) {
    ids.push(await call(pages[index],engineNames[index],'open'));
    addresses.push(await call(pages[index],engineNames[index],'address'));
  }
  for (let index=0;index<pages.length;index++) {
    const other=(index+1)%pages.length;
    const response=JSON.parse(await call(pages[index],engineNames[index],'request',addresses[other]));
    assert.equal(response.peer_id,ids[other]); assert.equal(response.payload,'synthetic engine exchange');
  }
} catch(error) { failure={stage,message:String(error.message).replace(/workspace-browser-v1\.[A-Za-z0-9_-]+|\bw2\.[A-Za-z0-9_-]+/g,'[invitation redacted]')}; }
finally {
  await Promise.allSettled(pages.map((page,index)=>call(page,engineNames[index],'close')));
  await Promise.allSettled(browsers.map(browser=>browser.close()));
  const report={passed:!failure,timestamp:new Date().toISOString(),mode,stages,...(failure?{failure}:{})};
  await mkdir(evidence,{recursive:true}); await writeFile(resolve(evidence,'report.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2)); if(failure)process.exitCode=1;
}
