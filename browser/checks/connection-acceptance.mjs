import assert from 'node:assert/strict';
import { createHash, createPrivateKey, createPublicKey, randomBytes, randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { chromium, webkit } from 'playwright';
import { build } from 'vite';

const checks = dirname(fileURLToPath(import.meta.url)), source = resolve(checks, '../..');
const option = (name, fallback) => { const i = process.argv.indexOf(name); return i < 0 ? fallback : process.argv[i + 1]; };
const evidence = resolve(option('--evidence', join(tmpdir(), `workspace-connections-${Date.now()}`)));
const legacyArgument = option('--legacy-dist', null);
const baseline = legacyArgument ? resolve(legacyArgument) : null;
const selectedCase = option('--case', 'all');
const engineName = option('--engine', 'chromium');
const commitReplyFault = process.argv.includes('--lose-migration-commit-reply');
assert(['migration', 'peers', 'all'].includes(selectedCase) && ['chromium', 'webkit'].includes(engineName));
await mkdir(dirname(evidence), { recursive: true }); await mkdir(evidence);
const scratch = await mkdtemp(join(tmpdir(), 'workspace-connection-check-'));
const fixture = join(scratch, 'fixture'), dist = join(scratch, 'dist');
await mkdir(fixture);
const report = { kind: 'browser-connections-acceptance-v1', passed: false, case: selectedCase, engine: engineName, started_at: new Date().toISOString(), assertions: [], storage_scans: [],
  limits: ['Synthetic-only identities and credentials in disposable browser profiles.', 'Desktop engine evidence is not a physical iPhone result.', 'No real transcription request is authorized; provider traffic is blocked.', 'No raw identity, credential, database, invitation or profile is exported.'] };
const peers = [];
let server, origin, stage = 'build';
const hash = value => createHash('sha256').update(value).digest('hex');
function record(name, condition = true) { assert(condition, name); report.assertions.push(name); console.log(JSON.stringify({ passed: name })); }
function mark(name) { stage = name; console.log(JSON.stringify({ stage })); }
async function within(promise, milliseconds, label) { let timer; try { return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label+' deadline')),milliseconds)})]); } finally { clearTimeout(timer); } }
async function files(root) {
  const paths = [];
  for (const item of await readdir(root, { withFileTypes: true })) {
    const path = join(root, item.name);
    if (item.isDirectory()) paths.push(...await files(path)); else if (item.isFile()) paths.push(path);
  }
  return paths.sort();
}
async function fingerprint() {
  const roots = ['browser/host/src', 'browser/host-wasm/src', 'browser/host-wasm/pkg'];
  const paths = ['browser/checks/page.ts', 'browser/checks/connection-acceptance.mjs', 'browser/host/package-lock.json', 'native/core/src/model.rs', 'native/core/src/work.rs', 'native/ui/src/protocol.ts', 'native/ui/src/meeting-preparation.ts'];
  for (const root of roots) for (const path of await files(join(source, root))) paths.push(relative(source, path));
  return Object.fromEntries(await Promise.all(paths.sort().map(async name => [name, hash(await readFile(join(source, name)))])));
}
const auditSource = `
import { connect } from ${JSON.stringify(join(source, 'browser/host/node_modules/@tursodatabase/database-wasm/dist/promise-default.js'))};
const request = value => new Promise((resolve,reject)=>{value.onsuccess=()=>resolve(value.result);value.onerror=()=>reject(new Error('Audit IndexedDB request failed'))});
async function database(name,work,options){const db=await connect(name,options);try{return await work(db)}finally{await db.close()}}
async function opfsFiles(){const found=[];async function visit(dir,prefix=''){for await(const [name,item]of dir.entries()){const path=prefix+name;if(item.kind==='directory')await visit(item,path+'/');else found.push({path,handle:item})}}await visit(await navigator.storage.getDirectory());return found}
window.connectionAudit={
 async createLegacy(name,seed){return database(name,async db=>{await db.exec('PRAGMA synchronous=FULL; CREATE TABLE identity(slot INTEGER PRIMARY KEY CHECK(slot=1),seed BLOB NOT NULL,name TEXT NOT NULL) STRICT; CREATE TABLE operations(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,payload TEXT NOT NULL) STRICT; CREATE TABLE requests(id TEXT PRIMARY KEY,payload TEXT NOT NULL) STRICT; CREATE TABLE peers(id TEXT PRIMARY KEY,payload TEXT NOT NULL) STRICT; CREATE TABLE invitations(token_hash TEXT PRIMARY KEY,workspace_ids TEXT NOT NULL,expires_at_ms INTEGER NOT NULL,accepted_peer TEXT) STRICT;');await(await db.prepare('INSERT INTO identity(slot,seed,name) VALUES(1,?,?)')).run(new Uint8Array(seed),'Synthetic legacy identity')})},
 async legacySeed(name){return database(name,async db=>Array.from((await(await db.prepare('SELECT seed FROM identity WHERE slot=1')).get()).seed))},
 async rows(name){return database(name,async db=>({seed_bytes:(await(await db.prepare('SELECT length(seed) AS n FROM identity WHERE slot=1')).get()).n,connections:await(await db.prepare('SELECT id,workspace_id,nonce,ciphertext FROM connections ORDER BY id')).all()}))},
 async identityDigest(name,truncate=false){return database(name,async db=>{if(truncate)await db.exec('UPDATE identity SET seed=substr(seed,1,32) WHERE slot=1');const seed=(await(await db.prepare('SELECT seed FROM identity WHERE slot=1')).get()).seed;const digest=new Uint8Array(await crypto.subtle.digest('SHA-256',seed));return{bytes:seed.length,fingerprint:Array.from(digest,value=>value.toString(16).padStart(2,'0')).join('')}})},
 async scan(probes){const result=[];for(const {path,handle}of await opfsFiles()){const bytes=new Uint8Array(await(await handle.getFile()).arrayBuffer());const matches=probes.map(probe=>{let count=0;outer:for(let i=0;i<=bytes.length-probe.bytes.length;i++){for(let j=0;j<probe.bytes.length;j++)if(bytes[i+j]!==probe.bytes[j])continue outer;count++}return{label:probe.label,count}});result.push({path,bytes:bytes.length,matches})}return result},
 async exportFiles(){return Promise.all((await opfsFiles()).map(async({path,handle})=>({path,bytes:Array.from(new Uint8Array(await(await handle.getFile()).arrayBuffer()))})))},
 async importFiles(entries){for(const entry of entries){const parts=entry.path.split('/');let directory=await navigator.storage.getDirectory();for(const name of parts.slice(0,-1))directory=await directory.getDirectoryHandle(name,{create:true});const handle=await directory.getFileHandle(parts.at(-1),{create:true});const writable=await handle.createWritable();await writable.write(new Uint8Array(entry.bytes));await writable.close()}},
 async deleteFiles(){const root=await navigator.storage.getDirectory();for await(const[name]of root.entries())await root.removeEntry(name,{recursive:true})},
 async deviceKey(name,mode){const opening=indexedDB.open('shared-workspace-device-keys',1);opening.onupgradeneeded=()=>opening.result.createObjectStore('connection-keys');const db=await request(opening);try{const tx=db.transaction('connection-keys',mode==='inspect'?'readonly':'readwrite');const finished=new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onabort=tx.onerror=()=>reject(new Error('Audit key transaction failed'))});const store=tx.objectStore('connection-keys');let state;if(mode==='delete')store.delete(name);else if(mode==='corrupt')store.put('synthetic corrupt key',name);else{const key=await request(store.get(name));state=key?{type:key.type,extractable:key.extractable,algorithm:key.algorithm,usages:key.usages}:null}await finished;return state}finally{db.close()}},
 async checkpoint(name){return database(name,async db=>{await db.exec('PRAGMA wal_checkpoint(TRUNCATE)');return true})},
 async sanitize(name){return database(name,async db=>{await db.exec('VACUUM');await db.exec('PRAGMA wal_checkpoint(TRUNCATE)');return true},{experimental:['vacuum']})}
};`;

async function compile() {
  await writeFile(join(fixture, 'index.html'), `<html><body><script type="module">import ${JSON.stringify(join(checks, 'page.ts'))};</script></body></html>`);
  await writeFile(join(fixture, 'audit.html'), '<html><body><script type="module" src="./audit.js"></script></body></html>');
  await writeFile(join(fixture, 'audit.js'), auditSource);
  await writeFile(join(fixture, 'legacy.html'), '<html><body>Disposable legacy host fixture</body></html>');
  const faultPlugin=()=>({name:'migration-commit-reply-loss',enforce:'pre',transform(code,id){if(!commitReplyFault||!id.endsWith('/browser/host/src/store.ts'))return;const marker=/await \(await db\.prepare\('UPDATE identity SET seed = \? WHERE slot = 1'\)\)\.run\(encrypted\);[\s\S]*?await db\.exec\('COMMIT'\);/;assert(marker.test(code),'Migration fault must target the actual identity commit');return code.replace(marker,match=>match+"\n          throw new Error('Synthetic migration commit reply loss');")}});
  await build({ root: fixture, configFile: false, logLevel: 'error', base: './', plugins:[faultPlugin()], worker: { format: 'es', plugins:()=>[faultPlugin()] }, build: { outDir: dist, emptyOutDir: true, target: 'esnext', rollupOptions: { input: { index: join(fixture, 'index.html'), audit: join(fixture, 'audit.html') } } }, optimizeDeps: { exclude: ['@tursodatabase/database-wasm'] } });
  if(commitReplyFault)report.fault_injection='Disposable build throws after the actual legacy identity COMMIT, before its reply is acknowledged; production sources unchanged.';
  report.assets = Object.fromEntries(await Promise.all((await files(dist)).map(async path => [relative(dist, path), hash(await readFile(path))])));
  if(baseline)report.legacy_assets = Object.fromEntries(await Promise.all((await files(baseline)).map(async path => [relative(baseline, path), hash(await readFile(path))])));
  server = createServer(async (request, response) => {
    try {
      const path = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      let root = dist, suffix = path === '/' ? '/index.html' : path;
      if (baseline&&path.startsWith('/shared-workspace/')) { root = baseline; suffix = '/' + path.slice('/shared-workspace/'.length); }
      if (path === '/legacy.html') { root = fixture; suffix = path; }
      const file = resolve(root, '.' + suffix);
      if (!file.startsWith(root + sep)) { response.writeHead(403).end(); return; }
      const body = await readFile(file);
      response.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm' }[extname(file)] ?? 'application/octet-stream', 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cache-Control': 'no-store' }).end(body);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)});
  origin = 'http://127.0.0.1:' + server.address().port;
}
class Peer {
  constructor(name) { this.name=name; this.profile=join(scratch,name); peers.push(this); }
  async start() {
    this.context=await ({chromium,webkit}[engineName]).launchPersistentContext(this.profile,{headless:true});
    this.context.setDefaultTimeout(30000);
    this.providerRequests=0;
    await this.context.route('https://api.openai.com/**',async route=>{this.providerRequests++;await route.abort()});
    this.page=this.context.pages()[0]??await this.context.newPage();
    await this.page.goto(origin);await this.page.waitForFunction(()=>Boolean(window.workspaceCheck));
    this.audit=await this.context.newPage();await this.audit.goto(origin+'/audit.html');await this.audit.waitForFunction(()=>Boolean(window.connectionAudit));
    return this;
  }
  options(paused=true) { return {deviceName:this.name,databaseName:'connection-check.db',baseUrl:origin+'/',startPaused:paused}; }
  async call(method,args=[]) { return within(this.page.evaluate(async({method,args})=>{try{if(method==='transcribe'){args[0]={...args[0],audio:new Blob(['synthetic unsupported audio'],{type:'audio/mp4'})}}return{ok:true,value:await window.workspaceCheck[method](...args)}}catch(error){return{ok:false,error:error instanceof Error?error.message:String(error)}}},{method,args}),60000,this.name+'.'+method); }
  async success(method,args=[]) { const response=await this.call(method,args);assert(response.ok, this.name+'.'+method+' must succeed');return response.value; }
  async inspect(method,args=[]) { return within(this.audit.evaluate(({method,args})=>window.connectionAudit[method](...args),{method,args}),30000,'Storage inspection'); }
  async stop() { if(this.context){await this.context.close();this.context=undefined} }
}
async function migration() {
  mark('legacy-host-create');
  const peer=await new Peer('migration').start();
  let oldSnapshot;
  if(baseline){
    const legacyAssets=await readdir(join(baseline,'assets'));const hostFile=legacyAssets.find(name=>/^host-.*\.js$/.test(name));assert(hostFile);
    const legacy=await peer.context.newPage();await legacy.goto(origin+'/legacy.html');
    oldSnapshot=await legacy.evaluate(async({url,options})=>{const{BrowserHost}=await import(url);window.legacyHost=await BrowserHost.open(options);await window.legacyHost.command({type:'create_workspace',operation_id:'legacy-work',workspace_id:'legacy',title:'Legacy test work'});await window.legacyHost.command({type:'create_resource',operation_id:'legacy-note',workspace_id:'legacy',resource_id:'note',title:'Retained note',body:'Synthetic work survives identity migration.'});return window.legacyHost.snapshot()},{url:origin+'/shared-workspace/assets/'+hostFile,options:peer.options()});
    await legacy.evaluate(()=>window.legacyHost.close());await legacy.close();
    report.legacy_fixture='actual retained old binary';
  }else{
    const seed=randomBytes(32);await peer.inspect('createLegacy',['connection-check.db',Array.from(seed)]);
    const privateKey=createPrivateKey({key:Buffer.concat([Buffer.from('302e020100300506032b657004220420','hex'),seed]),format:'der',type:'pkcs8'});
    oldSnapshot={device_id:createPublicKey(privateKey).export({format:'der',type:'spki'}).subarray(-32).toString('hex'),resources:[]};
    report.legacy_fixture='synthetic legacy SQL format with actual Turso 0.8.3';
  }
  const seed=await peer.inspect('legacySeed',['connection-check.db']);assert(seed.length===32);
  const probes=[{label:'legacy_identity_bytes',bytes:seed},{label:'legacy_identity_hex',bytes:Array.from(Buffer.from(Buffer.from(seed).toString('hex')))}];
  if(process.argv.includes('--checkpoint-legacy'))await peer.inspect('checkpoint',['connection-check.db']);
  const before=await peer.inspect('scan',[probes]);report.storage_scans.push({phase:'legacy-closed',files:before});
  record('Legacy fixture retains a discoverable plaintext identity seed',before.some(file=>file.matches.some(match=>match.count>0)));
  mark('current-host-migrate');
  if(commitReplyFault){const lost=await peer.call('open',[peer.options()]);record('Lost migration COMMIT reply refuses admission until storage is reopened',!lost.ok);}
  const migrated=await peer.success('open',[peer.options()]);
  const identityPreserved=migrated.device_id===oldSnapshot.device_id, workPreserved=isDeepStrictEqual(migrated.resources,oldSnapshot.resources);
  try {report.storage_scans.push({phase:'migrated-live',files:await peer.inspect('scan',[probes])});}catch{report.storage_scans.push({phase:'migrated-live',unavailable:true})}
  await peer.success('close');
  const after=await peer.inspect('scan',[probes]);report.storage_scans.push({phase:'migrated-closed',files:after});
  const retained=after.some(file=>file.matches.some(match=>match.count>0));
  report.migration={identity_preserved:identityPreserved,work_preserved:workPreserved,plaintext_retained:retained};
  record('Migration preserves the legacy identity and retained work',identityPreserved&&workPreserved);
  const rows=await peer.inspect('rows',['connection-check.db']);report.migration.identity_row_bytes=rows.seed_bytes;
  record('Migrated SQL identity row contains the encrypted envelope',rows.seed_bytes===64);
  await writeFile(join(evidence,'migration-before-probe.json'),JSON.stringify({migration:report.migration,storage_scans:report.storage_scans},null,2)+'\n');
  if(retained&&process.argv.includes('--probe-checkpoint')){
    mark('checkpoint-only-probe');
    try{await peer.inspect('checkpoint',['connection-check.db']);const cleaned=await peer.inspect('scan',[probes]);report.storage_scans.push({phase:'checkpoint-only-closed',files:cleaned});report.migration.checkpoint_removed_seed=!cleaned.some(file=>file.matches.some(match=>match.count>0));const reopened=await peer.success('open',[peer.options()]);report.migration.checkpoint_preserved_identity_and_work=reopened.device_id===oldSnapshot.device_id&&isDeepStrictEqual(reopened.resources,oldSnapshot.resources);await peer.success('close');}
    catch{report.migration.checkpoint_failed=true}
  }
  if(retained&&process.argv.includes('--probe-vacuum')){
    mark('experimental-sanitization-probe');
    try{await peer.inspect('sanitize',['connection-check.db']);const sanitized=await peer.inspect('scan',[probes]);report.storage_scans.push({phase:'experimental-vacuum-closed',files:sanitized});report.migration.experimental_sanitization_removed_seed=!sanitized.some(file=>file.matches.some(match=>match.count>0));}
    catch{report.migration.experimental_sanitization_failed=true}
  }
  record('Migration removes the old plaintext seed from current OPFS database and WAL files',!retained);
  return peer;
}
const operation = () => randomBytes(32).toString('hex');
const versions = state => state.status==='missing'?[]:state.status==='conflict'?state.versions:[state.version];
function noSecrets(value,secrets){const text=JSON.stringify(value);return secrets.every(secret=>!text.includes(secret))}
async function connectionPeers() {
  mark('fresh-scoped-peers');
  const a=await new Peer('owner').start(),b=await new Peer('member').start(),outsider=await new Peer('outsider').start();
  await a.success('open',[a.options()]);await b.success('open',[b.options()]);await outsider.success('open',[outsider.options()]);
  for(const [id,title]of [['shared','Shared synthetic work'],['private','Unshared synthetic work']])await a.success('command',[{type:'create_workspace',operation_id:randomUUID(),workspace_id:id,title}]);
  await a.success('command',[{type:'create_resource',operation_id:randomUUID(),workspace_id:'shared',resource_id:'note',title:'Peer note',body:'Original note'}]);
  const secrets=Array.from({length:6},()=>`synthetic-key-${randomBytes(24).toString('hex')}`);
  const change=(workspace,key,expected=[])=>({workspace_id:workspace,operation_id:operation(),expected_versions:expected,api_key:key,model:'gpt-4o-transcribe'});
  const firstInput=change('shared',secrets[0]),privateInput=change('private',secrets[1]);
  const first=await a.success('setConnection',[firstInput]);await a.success('setConnection',[privateInput]);
  record('New connection receives a durable ready receipt without exposing its key',first.state.status==='ready'&&!first.replayed&&noSecrets(first,secrets));
  await a.success('setForeground',[true]);await b.success('setForeground',[true]);
  const invitation=await a.success('invite',[['shared']]);
  const joined=await b.success('join',[invitation.ticket]);
  const memberFirst=await b.success('connection',['shared']);
  record('Joining a selected workspace also imports its separately synchronized connection',isDeepStrictEqual(memberFirst,first.state));
  record('Unshared workspace connection is inaccessible to the joined member',!(await b.call('connection',['private'])).ok);
  record('Unenrolled host cannot inspect the shared workspace connection',!(await outsider.call('connection',['shared'])).ok);
  const counts=[(await a.success('snapshot')).operation_count,joined.operation_count];
  const secondInput=change('shared',secrets[2],[first.state.version]);
  const second=await a.success('setConnection',[secondInput]);
  await b.success('sync');
  record('Credential-only updates synchronize without resource operations changing',isDeepStrictEqual(await b.success('connection',['shared']),second.state)&&isDeepStrictEqual([(await a.success('snapshot')).operation_count,(await b.success('snapshot')).operation_count],counts));
  const repeated=await a.success('setConnection',[secondInput]),oldRetry=await a.success('setConnection',[firstInput]);
  record('Exact retries acknowledge the durable identity and old retries return current metadata',repeated.replayed&&oldRetry.replayed&&isDeepStrictEqual(oldRetry.state,second.state));
  const changedRetry=await a.call('setConnection',[{...secondInput,api_key:secrets[3]}]);
  const stale=await a.call('setConnection',[change('shared',secrets[3],[first.state.version])]);
  record('Changed-input retries and stale connection writes reject without secret-bearing errors',!changedRetry.ok&&!stale.ok&&noSecrets([changedRetry,stale],secrets));
  mark('offline-connection-conflict');
  await a.success('setForeground',[false]);await b.success('setForeground',[false]);
  const offlineSet=await a.success('setConnection',[change('shared',secrets[3],[second.state.version])]);
  const offlineRemove=await b.success('removeConnection',[{workspace_id:'shared',operation_id:operation(),expected_versions:[second.state.version]}]);
  await a.success('setForeground',[true]);await b.success('setForeground',[true]);await a.success('sync');await b.success('sync');
  const conflict=await b.success('connection',['shared']);
  record('Offline set/remove edits preserve both conflicting versions',conflict.status==='conflict'&&isDeepStrictEqual([...conflict.versions].sort(),[offlineSet.state.version,offlineRemove.state.version].sort()));
  const conflictTranscription=await b.call('transcribe',[{workspace_id:'shared',connection_version:offlineSet.state.version,locale:''}]);
  record('Conflicted connection refuses transcription before provider upload',!conflictTranscription.ok&&b.providerRequests===0&&noSecrets(conflictTranscription,secrets));
  const resolved=await b.success('setConnection',[change('shared',secrets[4],versions(conflict))]);await b.success('sync');
  record('Explicitly resolving every conflict head converges the connection',resolved.state.status==='ready'&&isDeepStrictEqual(await a.success('connection',['shared']),resolved.state));
  const removeInput={workspace_id:'shared',operation_id:operation(),expected_versions:[resolved.state.version]};
  const removed=await b.success('removeConnection',[removeInput]);await b.success('sync');
  const removalRetry=await b.success('removeConnection',[removeInput]);
  record('Removal tombstone synchronizes and exact removal retry stays idempotent',removed.state.status==='removed'&&removalRetry.replayed&&isDeepStrictEqual(await a.success('connection',['shared']),removed.state));
  const removedTranscription=await a.call('transcribe',[{workspace_id:'shared',connection_version:resolved.state.version,locale:''}]);
  record('Removed connection cannot upload with a captured old version',!removedTranscription.ok&&a.providerRequests===0&&noSecrets(removedTranscription,secrets));
  const beforeRestart=await b.success('snapshot');await b.stop();await b.start();const afterRestart=await b.success('open',[b.options()]);
  record('Browser-process restart retains identity, work and connection tombstone',beforeRestart.device_id===afterRestart.device_id&&isDeepStrictEqual(beforeRestart.resources,afterRestart.resources)&&isDeepStrictEqual(await b.success('connection',['shared']),removed.state));
  record('Generic workspace snapshots never expose connection values',noSecrets([await a.success('snapshot'),afterRestart],secrets)&&!JSON.stringify(afterRestart).includes('api_key'));
  const readyAgain=await a.success('setConnection',[change('shared',secrets[5],[removed.state.version])]);
  await b.success('setForeground',[true]);await b.success('sync');
  record('Foreground reconnection synchronizes a replacement after removal',isDeepStrictEqual(await b.success('connection',['shared']),readyAgain.state));
  mark('connection-ciphertext-at-rest');
  await a.success('close');await b.success('close');
  const probes=secrets.map((value,index)=>({label:'synthetic_api_key_'+index,bytes:Array.from(Buffer.from(value))}));
  for(const peer of [a,b]){
    const raw=await peer.inspect('scan',[probes]);report.storage_scans.push({phase:peer.name+'-connection-closed',files:raw});
    const rows=await peer.inspect('rows',['connection-check.db']);
    record(peer.name+' persists encrypted identity and connection rows without plaintext synthetic keys',rows.seed_bytes===64&&rows.connections.length>0&&noSecrets(rows,secrets)&&raw.every(file=>file.matches.every(match=>match.count===0)));
    const key=await peer.inspect('deviceKey',['connection-check.db','inspect']);
    record(peer.name+' retains a nonextractable AES256 device key',key.type==='secret'&&!key.extractable&&key.algorithm.name==='AES-GCM'&&key.algorithm.length===256);
  }
  if(baseline){
    mark('actual-old-peer-compatibility');
    await a.success('open',[a.options(false)]);
    await b.success('open',[b.options(false)]);
    const old=await new Peer('old-note-peer').start();
    const hostFile=(await readdir(join(baseline,'assets'))).find(name=>/^host-.*\.js$/.test(name));
    await old.page.goto(origin+'/legacy.html');
    await old.page.evaluate(async({url,options})=>{const{BrowserHost}=await import(url);window.workspaceCheck=await BrowserHost.open(options)},{url:origin+'/shared-workspace/assets/'+hostFile,options:old.options(false)});
    const ticket=await a.success('invite',[['shared']]);await old.success('join',[ticket.ticket]);await a.success('sync');
    const oldSnapshot=await old.success('snapshot');
    const status=await a.success('connectionPeers',['shared']);
    record('Actual note-only baseline joins while connection metadata reports upgrade required',oldSnapshot.resources.some(item=>item.id==='note')&&status.some(item=>item.peer_id===oldSnapshot.device_id&&item.status==='upgrade_required'));
    const head=oldSnapshot.resources.find(item=>item.id==='note').heads.map(item=>item.id);
    await old.success('command',[{type:'edit_resource',operation_id:randomUUID(),workspace_id:'shared',resource_id:'note',expected_heads:head,title:'Peer note',body:'Old peer note update'}]);await old.success('sync');
    record('Old peer note edits continue across unsupported connection capability',(await a.success('snapshot')).resources.find(item=>item.id==='note').heads[0].body==='Old peer note update');
    await old.success('close');const scan=await old.inspect('scan',[probes]);report.storage_scans.push({phase:'old-peer-closed',files:scan});
    record('Actual old peer persisted no synthetic connection key',scan.every(file=>file.matches.every(match=>match.count===0)));
    await a.success('close');
    await b.success('close');
  }
  mark('missing-key-fail-closed');
  const empty=await new Peer('empty-encrypted-identity').start();await empty.success('open',[empty.options()]);await empty.success('close');
  const emptyFiles=await empty.inspect('exportFiles');const clone=await new Peer('ciphertext-only-clone').start();await clone.inspect('importFiles',[emptyFiles]);
  const cloneOpen=await clone.call('open',[clone.options()]);
  record('Ciphertext-only OPFS clone cannot open without its IndexedDB key even with no connections',!cloneOpen.ok);
  await empty.inspect('deviceKey',['connection-check.db','delete']);
  const missingOpen=await empty.call('open',[empty.options()]);
  record('Deleting a device key never silently replaces an encrypted empty identity',!missingOpen.ok);
  await a.inspect('deviceKey',['connection-check.db','corrupt']);
  const corruptOpen=await a.call('open',[a.options()]);
  record('Corrupt retained key refuses startup and errors disclose no synthetic secrets',!corruptOpen.ok&&noSecrets([cloneOpen,missingOpen,corruptOpen],secrets));
  const missingFiles=await new Peer('retained-key-missing-files').start();
  const identity=await missingFiles.success('open',[missingFiles.options()]);await missingFiles.success('close');
  const retainedFiles=await missingFiles.inspect('exportFiles');await missingFiles.inspect('deleteFiles');
  const withoutFiles=await missingFiles.call('open',[missingFiles.options()]);
  record('OPFS-only deletion with a retained device key refuses to create a replacement identity',!withoutFiles.ok&&noSecrets(withoutFiles,secrets));
  await missingFiles.inspect('deleteFiles');await missingFiles.inspect('importFiles',[retainedFiles]);
  const restored=await missingFiles.success('open',[missingFiles.options()]);
  record('Restoring original ciphertext after failed admission recovers the unchanged identity',restored.device_id===identity.device_id);
  const truncated=await new Peer('truncated-encrypted-identity').start();await truncated.success('open',[truncated.options()]);await truncated.success('close');
  const damaged=await truncated.inspect('identityDigest',['connection-check.db',true]);
  const damagedOpen=await truncated.call('open',[truncated.options()]);
  const afterRefusal=await truncated.inspect('identityDigest',['connection-check.db']);
  record('Truncated encrypted identity refuses startup rather than being reinterpreted as a legacy seed',damaged.bytes===32&&!damagedOpen.ok&&isDeepStrictEqual(afterRefusal,damaged));
  report.provider_requests=peers.reduce((sum,peer)=>sum+(peer.providerRequests??0),0);
  record('No synthetic credential or audio reached the real provider',report.provider_requests===0);
}
try {
  report.source_hashes=await fingerprint();await compile();
  if(selectedCase!=='peers')await migration();
  if(selectedCase!=='migration')await connectionPeers();
  report.passed=true;
} catch(error) { report.failure={stage,assertion:error instanceof assert.AssertionError?error.message:'The browser acceptance operation failed.'}; }
finally {
  const closed=await Promise.allSettled(peers.map(peer=>peer.stop()));
  let serverClosed=false;try{if(server)await new Promise(resolve=>server.close(resolve));serverClosed=true}catch{}
  let removed=false;try{await rm(scratch,{recursive:true,force:true});removed=true}catch{}
  report.cleanup={browsers_closed:closed.every(result=>result.status==='fulfilled'),server_closed:serverClosed,profiles_removed:removed};
  report.source_unchanged=JSON.stringify(await fingerprint())===JSON.stringify(report.source_hashes);
  report.finished_at=new Date().toISOString();
  if(!Object.values(report.cleanup).every(Boolean)||!report.source_unchanged)report.passed=false;
  await writeFile(join(evidence,'report.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({passed:report.passed,assertions:report.assertions.length,migration:report.migration,failure:report.failure,report:join(evidence,'report.json')}));
  if(!report.passed)process.exitCode=1;
}
