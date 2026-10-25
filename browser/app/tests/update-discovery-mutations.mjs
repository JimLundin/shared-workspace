/** Targeted behavioral mutants in disposable source copies. */
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
const sourceUrl = new URL('../src/update-discovery.ts', import.meta.url), testsUrl = new URL('../src/update-discovery.test.ts', import.meta.url);
const source = await readFile(sourceUrl, 'utf8'), tests = await readFile(testsUrl, 'utf8');
const hash = value => createHash('sha256').update(value).digest('hex');
const mutations = [
  { id:'poll-hidden-display', from:'!observation.visible', to:'false', test:'hidden and offline displays perform no polls and resume automatically when eligible', marker:'HIDDEN_DISPLAY_POLLED' },
  { id:'poll-offline-display', from:'!observation.online', to:'false', test:'hidden and offline displays perform no polls and resume automatically when eligible', marker:'OFFLINE_DISPLAY_POLLED' },
  { id:'skip-wake-throttle', from:'UPDATE_THROTTLE_MS = 60_000', to:'UPDATE_THROTTLE_MS = 0', test:'repeated foreground and online events share a sixty second throttle', marker:'WAKE_THROTTLE_REMOVED' },
  { id:'poll-every-minute', from:'UPDATE_INTERVAL_MS = 5 * 60_000', to:'UPDATE_INTERVAL_MS = 60_000', test:'startup checks immediately then periodic discovery waits five visible minutes', marker:'PERIODIC_CHECK_TOO_EARLY' },
  { id:'overlap-update-checks', from:'const plan = planUpdateCheck(attempt,', to:"if (attempt.type === 'checking') attempt = { type: 'idle', lastAttemptAt: null };\n    const plan = planUpdateCheck(attempt,", test:'update checks remain single flight through repeated lifecycle events', marker:'OVERLAPPING_UPDATE_CHECK' },
  { id:'publish-after-disposal', from:"if (attempt.type === 'disposed') return;\n      attempt =", to:'attempt =', test:'disposal ignores late completions and cannot revive from events or timers', marker:'DISPOSED_CHECK_PUBLISHED' },
  { id:'poll-in-bfcache', from:'active = false; clearTimer();', to:'clearTimer();', test:'BFCache suspension ignores wake events and restores one automatic check', marker:'BFCACHE_DISPLAY_POLLED' },
  { id:'fail-bfcache-resume', from:"active = true; step('wake');", to:'active = true;', test:'BFCache suspension ignores wake events and restores one automatic check', marker:'BFCACHE_RESUME_MISSING' },
];
const temporary = await mkdtemp(join(tmpdir(), 'workspace-update-mutants-'));
const report = { kind:'workspace-update-discovery-mutations-v1', node:process.version, source_sha256:hash(source), tests_sha256:hash(tests), harness_sha256:hash(await readFile(import.meta.filename)), baseline:null, mutations:[], source_unchanged:false, passed:false, scope:'Pure scheduler and lifecycle effect owner with an injected clock. Real service-worker behavior has separate update.mjs acceptance.' };
function run(test) { const args=['--experimental-strip-types','--test','--test-reporter=tap'];if(test)args.push('--test-name-pattern','^'+test.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$');args.push('update-discovery.test.ts');const child=spawnSync(process.execPath,args,{cwd:temporary,encoding:'utf8',timeout:10000});return{exit_code:child.status,signal:child.signal,error:child.error?.code??null,output:(child.stdout??'')+(child.stderr??'')}; }
try {
  await writeFile(join(temporary,'package.json'),'{"type":"module"}');await writeFile(join(temporary,'update-discovery.test.ts'),tests);await writeFile(join(temporary,'update-discovery.ts'),source);
  report.baseline=run();if(report.baseline.exit_code!==0||report.baseline.signal||report.baseline.error)throw new Error('Passing baseline required.');
  for(const mutation of mutations){if(source.split(mutation.from).length!==2)throw new Error('Ambiguous mutation: '+mutation.id);const changed=source.replace(mutation.from,mutation.to);await writeFile(join(temporary,'update-discovery.ts'),changed);const result=run(mutation.test);const killed=result.exit_code===1&&!result.signal&&!result.error&&result.output.includes('ERR_ASSERTION')&&result.output.includes(mutation.marker)&&/# tests 1\b/.test(result.output)&&/# fail 1\b/.test(result.output);report.mutations.push({...mutation,...result,mutated_sha256:hash(changed),outcome:killed?'killed-by-intended-assertion':result.exit_code===0?'survived':'invalid-or-unrelated-failure'});console.log(JSON.stringify({id:mutation.id,killed}));}
  report.source_unchanged=source===await readFile(sourceUrl,'utf8')&&tests===await readFile(testsUrl,'utf8');report.passed=report.source_unchanged&&report.mutations.length===mutations.length&&report.mutations.every(item=>item.outcome==='killed-by-intended-assertion');
}catch(error){report.failure=String(error)}finally{await rm(temporary,{recursive:true,force:true});const path=resolve(process.argv[2]??'/tmp/workspace-update-discovery-mutants.json');await mkdir(dirname(path),{recursive:true});await writeFile(path,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({passed:report.passed,killed:report.mutations.filter(item=>item.outcome==='killed-by-intended-assertion').length,total:mutations.length,report:path}));if(!report.passed)process.exitCode=1;}
