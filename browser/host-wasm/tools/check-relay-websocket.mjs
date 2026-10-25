import { chromium, webkit } from '../../checks/node_modules/playwright/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const args=process.argv.slice(2);const out=resolve(args[args.indexOf('--evidence')+1]||'/tmp/relay-websocket');
const results=[];
for(const [engine,type] of [['chromium',chromium],['webkit',webkit]]){
  const browser=await type.launch({headless:true});
  try{
    const context=await browser.newContext();
    await context.route('https://relay-probe.invalid/**',route=>route.fulfill({contentType:'text/html',headers:{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'},body:'<!doctype html><title>Relay websocket check</title>'}));
    const page=await context.newPage();await page.goto('https://relay-probe.invalid/');
    for(const hostname of ['euc1-1.relay.n0.iroh.link.','euc1-1.relay.n0.iroh.link']){
      const result=await page.evaluate(hostname=>new Promise(resolve=>{
        const started=performance.now();let socket;let done=false;
        const finish=(state,details={})=>{if(done)return;done=true;clearTimeout(timer);socket?.close();resolve({state,duration_ms:Math.round(performance.now()-started),...details});};
        const timer=setTimeout(()=>finish('timeout'),5000);
        try{
          socket=new WebSocket(`wss://${hostname}/relay`,['iroh-relay-v2','iroh-relay-v1']);
          socket.onopen=()=>finish('open',{protocol:socket.protocol});
          socket.onerror=()=>finish('error');
          socket.onclose=event=>finish('closed',{code:event.code});
        }catch(error){finish('constructor-error',{name:error.name});}
      }),hostname);
      const observation={engine,version:browser.version(),hostname,...result};results.push(observation);console.log(JSON.stringify(observation));
    }
  }finally{await browser.close();}
}
await mkdir(out,{recursive:true});await writeFile(resolve(out,'report.json'),JSON.stringify({timestamp:new Date().toISOString(),scope:'Only real TLS/WebSocket upgrade, before Iroh authentication; no certificate exceptions',results},null,2)+'\n');
