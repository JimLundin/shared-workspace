const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('playwright');

const ROOT = '/evidence';
const TARGETS = {drawio: 'https://embed.diagrams.net/?proto=json&spin=1&ui=min&libraries=1'};
const INITIAL_XML = '<mxGraphModel dx="1000" dy="700" grid="1" gridSize="10"><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="fixture" value="Toolkit embed before" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1"><mxGeometry x="100" y="100" width="200" height="80" as="geometry"/></mxCell></root></mxGraphModel>';
const fixtures = new Map();
const evidence = [];
const SAFE_HEADERS = ['content-type', 'x-frame-options', 'content-security-policy', 'cross-origin-opener-policy', 'cross-origin-embedder-policy', 'cross-origin-resource-policy'];
const noQuery = value => { try { const u = new URL(value); return u.origin + u.pathname; } catch { return String(value).slice(0,500); } };
const sanitize = value => String(value).replace(/https?:\/\/[^\s"'<>]+/g, value => noQuery(value)).slice(0,1600);
const encodeScript = value => JSON.stringify(value).replace(/</g, '\\u003c');
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');

function documentFor(config) {
  return `<!doctype html><html lang="en"><meta charset="utf-8"><title>Toolkit iframe isolation check</title><style>body{margin:0;font:14px system-ui;background:#eef1f5}header{height:52px;box-sizing:border-box;padding:14px;background:#152334;color:white}iframe{border:0;position:absolute;top:52px;left:0;width:100%;height:calc(100% - 52px)}</style><header>Public app embed check · ${config.name} · ${config.credentialless ? 'credentialless requested' : 'ordinary iframe'}</header><script>
  const config = ${encodeScript(config)};
  window.observed = {events: [], documents: [], init: false, loaded: false, frameLoads: 0, credentialsSupported: 'credentialless' in HTMLIFrameElement.prototype, isolatedBefore: crossOriginIsolated, sharedArrayBuffer: typeof SharedArrayBuffer, opfsApi: !!navigator.storage?.getDirectory};
  const frame = document.createElement('iframe');
  frame.id = 'app';
  frame.title = 'Public application surface';
  if (config.credentialless) frame.setAttribute('credentialless', '');
  const origin = new URL(config.url).origin;
  window.sendToApp = message => frame.contentWindow.postMessage(JSON.stringify(message), origin);
  const acceptedEvents = new Set(['init','load','autosave','save','export']);
  window.addEventListener('message', event => {
    if (event.source !== frame.contentWindow || event.origin !== origin) return;
    if (typeof event.data === 'string' && event.data.length > 1048576) return;
    let message;
    try { message = typeof event.data === 'string' ? JSON.parse(event.data) : event.data; } catch { return; }
    if (!message || typeof message !== 'object' || !acceptedEvents.has(message.event)) return;
    if (message.xml !== undefined && (typeof message.xml !== 'string' || message.xml.length > 262144)) return;
    if (message.data !== undefined && (typeof message.data !== 'string' || message.data.length > 1048576)) return;
    window.observed.events.push({event: message.event || null, keys: Object.keys(message), xmlLength: typeof message.xml === 'string' ? message.xml.length : null, dataLength: typeof message.data === 'string' ? message.data.length : null});
    if (typeof message.xml === 'string') window.observed.documents.push({event: message.event, xml: message.xml});
    if (message.event === 'init' && !window.observed.init) {
      window.observed.init = true;
      window.sendToApp({action:'load',xml:config.xml,title:'Disposable Toolkit embed check',autosave:1});
    }
    if (message.event === 'load') window.observed.loaded = true;
    if (message.event === 'export' && typeof message.data === 'string') window.observed.exportData = message.data;
  });
  frame.addEventListener('load', () => { window.observed.frameLoads++; });
  frame.src = config.url;
  document.body.append(frame);
  </script></html>`;
}

const server = http.createServer((req, res) => {
  const id = new URL(req.url, 'http://fixture').searchParams.get('id');
  const config = fixtures.get(id);
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
  res.setHeader('Cache-Control', 'no-store');
  if (!config) { res.writeHead(404, {'Content-Type':'text/plain'}); res.end('No fixture'); return; }
  res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
  res.end(documentFor(config));
});

async function runFrameCase(browser, engine, name, credentialless, options = {}) {
  const id = crypto.randomUUID();
  const label = `probe-${engine}-${name}-${credentialless ? 'credentialless' : 'ordinary'}${options.restore ? '-restore' : ''}`;
  fixtures.set(id, {name, credentialless, url:TARGETS[name], xml:options.xml || INITIAL_XML});
  const context = await browser.newContext({viewport:{width:1280,height:900}, locale:'en-US', acceptDownloads:false});
  const page = await context.newPage();
  const result = {engine, browserVersion:browser.version(), name, label, requestedUrl:TARGETS[name], mode:credentialless ? 'credentialless' : 'ordinary', restore:!!options.restore, documentResponses:[], failedRequests:[], consoleErrors:[], pageErrors:[]};
  const pending = [];
  page.on('response', response => {
    if (response.request().resourceType() !== 'document') return;
    pending.push((async () => {
      const all = await response.allHeaders();
      result.documentResponses.push({url:noQuery(response.url()),status:response.status(),headers:Object.fromEntries(SAFE_HEADERS.filter(key=>all[key]).map(key=>[key,all[key]]))});
    })().catch(error=>{ result.responseCaptureError=sanitize(error.message); }));
  });
  page.on('requestfailed', request => {
    if (result.failedRequests.length < 35) result.failedRequests.push({url:noQuery(request.url()),type:request.resourceType(),error:sanitize(request.failure()?.errorText)});
  });
  page.on('console', message => { if (message.type()==='error' && result.consoleErrors.length<25) result.consoleErrors.push(sanitize(message.text())); });
  page.on('pageerror', error=>{ if(result.pageErrors.length<15) result.pageErrors.push(sanitize(error.message)); });
  try {
    await page.goto(`http://127.0.0.1:${server.address().port}/?id=${id}`,{waitUntil:'domcontentloaded',timeout:20000});
    await page.waitForTimeout(2300);
    if (name === 'drawio') {
      const blocked = result.failedRequests.some(r=>r.type==='document' && /BLOCKED|blocked|restricted|embedder/i.test(r.error));
      if (!blocked) await page.waitForFunction(()=>window.observed.init,undefined,{timeout:35000}).catch(error=>{result.initWaitError=sanitize(error.message);});
      if (await page.evaluate(()=>window.observed.init)) {
        const frame = page.frames().find(frame=>frame.url().startsWith('https://embed.diagrams.net/'));
        if (frame) {
          await frame.getByText(options.restore ? 'Toolkit embed after' : 'Toolkit embed before',{exact:true}).first().waitFor({state:'visible',timeout:25000});
          result.visibleInitialLabel = true;
          if (options.restore) {
            result.restoredMarkerVisible = await frame.getByText('Toolkit embed after',{exact:true}).first().isVisible();
          } else {
            await frame.getByText('Toolkit embed before',{exact:true}).first().dblclick({timeout:10000});
            const editor = frame.locator('[contenteditable="true"]').filter({visible:true}).first();
            await editor.fill('Toolkit embed after',{timeout:8000});
            await editor.press('Control+Enter');
            await frame.getByText('Toolkit embed after',{exact:true}).first().waitFor({state:'visible',timeout:10000});
            result.userEditedMarkerVisible = true;
            await page.keyboard.press('Control+s');
            await page.waitForFunction(()=>window.observed.documents.some(d=>d.xml.includes('Toolkit embed after')),undefined,{timeout:12000}).catch(error=>{result.saveWaitError=sanitize(error.message);});
            const docs = await page.evaluate(()=>window.observed.documents.filter(d=>d.xml.includes('Toolkit embed after')));
            if (docs.length) {
              const saved = docs.at(-1);
              const file = `${label}-after.xml`;
              await fs.writeFile(path.join(ROOT,file),saved.xml,'utf8');
              result.savedDocument = {file,event:saved.event,bytes:Buffer.byteLength(saved.xml),sha256:sha256(saved.xml),containsEditedMarker:true};
              result.reopenXml = await fs.readFile(path.join(ROOT,file),'utf8');
            }
            await page.evaluate(()=>window.sendToApp({action:'export',format:'xmlsvg'}));
            await page.waitForFunction(()=>typeof window.observed.exportData==='string',undefined,{timeout:10000}).catch(error=>{result.exportWaitError=sanitize(error.message);});
            const data = await page.evaluate(()=>window.observed.exportData || null);
            if (data) {
              const file = `${label}-export.txt`;
              await fs.writeFile(path.join(ROOT,file),data,'utf8');
              result.export = {file,bytes:Buffer.byteLength(data),sha256:sha256(data),prefix:data.slice(0,60)};
            }
          }
        }
      }
    }
    result.frames = await Promise.all(page.frames().map(async frame => {
      const entry={url:noQuery(frame.url()),main:frame===page.mainFrame()};
      if (!entry.main) {
        try { entry.bodyText=(await frame.locator('body').innerText({timeout:1000})).slice(0,650); } catch { entry.bodyUnavailable=true; }
      }
      return entry;
    }));
    result.parent = await page.evaluate(()=>({...window.observed,documents:window.observed.documents.map(d=>({event:d.event,length:d.xml.length,edited:d.xml.includes('Toolkit embed after')})),exportData:undefined,isolatedAfter:crossOriginIsolated,frameCredentialless:document.querySelector('iframe').credentialless ?? null}));
    await page.screenshot({path:path.join(ROOT,`${label}.png`)});
    result.screenshot=`${label}.png`;
  } catch(error) {
    result.error=sanitize(error.message);
    try { result.parent=await page.evaluate(()=>({...window.observed,documents:window.observed.documents?.map(d=>({event:d.event,length:d.xml.length,edited:d.xml.includes('Toolkit embed after')})),exportData:undefined,isolatedAfter:crossOriginIsolated})); } catch {}
    try { await page.screenshot({path:path.join(ROOT,`${label}-error.png`)});result.screenshot=`${label}-error.png`; } catch {}
  } finally {
    await Promise.allSettled(pending);
    await context.close();
    fixtures.delete(id);
  }
  evidence.push(result);
  console.log(JSON.stringify({kind:'case',engine,name,mode:result.mode,restore:result.restore,isolated:result.parent?.isolatedAfter,credentiallessSupported:result.parent?.credentialsSupported,init:result.parent?.init,edited:result.userEditedMarkerVisible,saved:!!result.savedDocument,restored:result.restoredMarkerVisible,error:result.error,blocked:result.failedRequests.filter(r=>r.type==='document')}));
  return result;
}


(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:false});
  try {
    await runFrameCase(browser,'chromium','drawio',false);
    const edited=await runFrameCase(browser,'chromium','drawio',true);
    if (edited.reopenXml) {
      const xml=edited.reopenXml;
      delete edited.reopenXml;
      await runFrameCase(browser,'chromium','drawio',true,{restore:true,xml});
    }
    const restored=evidence.some(result=>result.restoredMarkerVisible);
    if (!edited.savedDocument || !restored) process.exitCode=1;
  } finally {
    await browser.close();
    await fs.writeFile(path.join(ROOT,'probe-drawio-results.json'),JSON.stringify(evidence,null,2)+'\n');
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
