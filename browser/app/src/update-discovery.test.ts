import assert from 'node:assert/strict';
import test from 'node:test';
import { discoverUpdates, planUpdateCheck, UPDATE_INTERVAL_MS, UPDATE_THROTTLE_MS } from './update-discovery.ts';
import type { UpdateEnvironment } from './update-discovery.ts';
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
function deferred() { let resolve!:()=>void; let reject!:(reason?:unknown)=>void; const promise=new Promise<void>((yes,no)=>{resolve=yes;reject=no});return{promise,resolve,reject}; }
class Environment implements UpdateEnvironment {
  time = 0; shown = true; connected = true; serial = 0;
  window = new EventTarget(); document = new EventTarget();
  timers = new Map<number,{at:number;callback:()=>void}>();
  now = () => this.time; visible = () => this.shown; online = () => this.connected;
  setTimer=(callback:()=>void,delayMs:number)=>{const id=++this.serial;this.timers.set(id,{at:this.time+delayMs,callback});return id};
  clearTimer=(id:number)=>{this.timers.delete(id)};
  async advance(ms:number){const end=this.time+ms;let iterations=0;while(true){const next=[...this.timers].filter(([,timer])=>timer.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;if(++iterations>100)throw new Error('Unbounded timer loop');this.time=next[1].at;this.timers.delete(next[0]);next[1].callback();await flush()}this.time=end;await flush()}
  visibility(value:boolean){this.shown=value;this.document.dispatchEvent(new Event('visibilitychange'))}
  connectivity(value:boolean){this.connected=value;this.window.dispatchEvent(new Event(value?'online':'offline'))}
  page(type:'pagehide'|'pageshow',persisted:boolean){const event=new Event(type);Object.defineProperty(event,'persisted',{value:persisted});this.window.dispatchEvent(event)}
}
function setup(environment=new Environment(), request:()=>Promise<unknown>=async()=>{}){let calls=0,observed=0;const discovery=discoverUpdates(()=>{calls++;return request()},()=>{observed++},environment);return{environment,discovery,calls:()=>calls,observed:()=>observed}}

test('startup checks immediately then periodic discovery waits five visible minutes',async()=>{
 const h=setup();await flush();assert.equal(h.calls(),1);await h.environment.advance(299999);assert.equal(h.calls(),1,'PERIODIC_CHECK_TOO_EARLY');await h.environment.advance(1);assert.equal(h.calls(),2,'PERIODIC_CHECK_MISSING');h.discovery.dispose();
});
test('repeated foreground and online events share a sixty second throttle',async()=>{
 const h=setup();await flush();await h.environment.advance(10000);for(let i=0;i<10;i++){h.environment.visibility(true);h.environment.connectivity(true)}await flush();assert.equal(h.calls(),1,'WAKE_THROTTLE_REMOVED');await h.environment.advance(49999);assert.equal(h.calls(),1);await h.environment.advance(1);assert.equal(h.calls(),2);h.discovery.dispose();
});
test('hidden and offline displays perform no polls and resume automatically when eligible',async()=>{
 const h=setup();await flush();h.environment.visibility(false);await h.environment.advance(900000);assert.equal(h.calls(),1,'HIDDEN_DISPLAY_POLLED');h.environment.connectivity(false);h.environment.visibility(true);await h.environment.advance(900000);assert.equal(h.calls(),1,'OFFLINE_DISPLAY_POLLED');h.environment.connectivity(true);await flush();assert.equal(h.calls(),2);h.discovery.dispose();
});
test('a display first opened in the background waits for visibility',async()=>{
 const e=new Environment();e.shown=false;const h=setup(e);await e.advance(900000);assert.equal(h.calls(),0);e.visibility(true);await flush();assert.equal(h.calls(),1);h.discovery.dispose();
});
test('update checks remain single flight through repeated lifecycle events',async()=>{
 const pending=deferred();let first=true;const h=setup(new Environment(),()=>{if(first){first=false;return pending.promise}return Promise.resolve()});await h.environment.advance(600000);h.environment.connectivity(true);h.environment.visibility(true);await flush();assert.equal(h.calls(),1,'OVERLAPPING_UPDATE_CHECK');pending.resolve();await flush();assert.equal(h.calls(),2);h.discovery.dispose();
});
test('a failed check waits for the normal schedule instead of retrying continuously',async()=>{
 const h=setup(new Environment(),async()=>{throw new Error('Synthetic unavailable network')});await flush();assert.equal(h.calls(),1);await h.environment.advance(UPDATE_INTERVAL_MS-1);assert.equal(h.calls(),1);await h.environment.advance(1);assert.equal(h.calls(),2);h.discovery.dispose();
});
test('BFCache suspension ignores wake events and restores one automatic check',async()=>{
 const h=setup();await flush();h.environment.page('pagehide',true);await h.environment.advance(900000);h.environment.connectivity(true);await flush();assert.equal(h.calls(),1,'BFCACHE_DISPLAY_POLLED');h.environment.page('pageshow',true);await flush();assert.equal(h.calls(),2,'BFCACHE_RESUME_MISSING');h.discovery.dispose();
});
test('disposal ignores late completions and cannot revive from events or timers',async()=>{
 const pending=deferred();const h=setup(new Environment(),()=>pending.promise);h.discovery.dispose();pending.resolve();await flush();assert.equal(h.observed(),0,'DISPOSED_CHECK_PUBLISHED');h.environment.visibility(true);h.environment.connectivity(true);h.environment.page('pageshow',true);await h.environment.advance(900000);assert.equal(h.calls(),1,'DISPOSED_DISCOVERY_REVIVED');
});
test('ordinary page departure permanently disposes the discovery owner',async()=>{
 const h=setup();await flush();h.environment.page('pagehide',false);h.environment.page('pageshow',true);h.environment.connectivity(true);await h.environment.advance(900000);assert.equal(h.calls(),1,'DEPARTED_PAGE_REVIVED');
});
test('pure scheduling respects eligibility and exact throttle boundaries',()=>{
 const observation={now:UPDATE_THROTTLE_MS-1,visible:true,online:true,active:true,trigger:'wake' as const};assert.deepEqual(planUpdateCheck({type:'idle',lastAttemptAt:0},observation),{type:'wait',delayMs:1});assert.deepEqual(planUpdateCheck({type:'idle',lastAttemptAt:0},{...observation,now:UPDATE_THROTTLE_MS}),{type:'check',at:UPDATE_THROTTLE_MS});assert.deepEqual(planUpdateCheck({type:'checking',startedAt:0},{...observation,now:999999}),{type:'wait',delayMs:null});assert.deepEqual(planUpdateCheck({type:'disposed'},observation),{type:'wait',delayMs:null});
});
