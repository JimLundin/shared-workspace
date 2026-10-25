// Runs assertion-targeted mutants in disposable copies, never the shared source.
import {mkdtemp,cp,symlink,readFile,writeFile,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {resolve,join} from 'node:path';import {spawnSync} from 'node:child_process';import {createHash} from 'node:crypto';
const root=resolve(import.meta.dirname,'..');
const cases=[
 {name:'stale editor adopts unseen heads',file:'session.ts',test:'session.test.ts',pattern:'stale editor preserves',from:'expected_heads: [...(reviewedHeads ?? e.baseHeads)]',to:'expected_heads: [...(reviewedHeads ?? (this.snapshot()?.resources.find(r => r.id === e.resourceId)?.heads.map(h => h.id) ?? e.baseHeads))]'},
 {name:'retry substitutes a new operation',file:'session.ts',test:'session.test.ts',pattern:'retry an unconfirmed save',from:'await this.submit(pending.command);',to:'await this.submit({ ...pending.command, operation_id: this.id() });'},
 {name:'late voice session loses its epoch fence',file:'voice-session.ts',test:'voice.test.ts',pattern:'late recognition cannot alter',from:'if (this.epoch !== epoch || event.session_id !== sessionId) return;',to:'if (event.session_id !== sessionId) return;'},
];
const results=[];
for(const item of cases){const scratch=await mkdtemp(join(tmpdir(),'workspace-ui-mutation-'));try{
 await cp(join(root,'src'),join(scratch,'src'),{recursive:true});await cp(join(root,'package.json'),join(scratch,'package.json'));await symlink(join(root,'node_modules'),join(scratch,'node_modules'),'dir');
 const path=join(scratch,'src',item.file),source=await readFile(path,'utf8');if(source.split(item.from).length!==2)throw new Error('Mutation target is missing or ambiguous: '+item.name);await writeFile(path,source.replace(item.from,item.to));
 const run=spawnSync(process.execPath,['--experimental-strip-types','--test','--test-name-pattern',item.pattern,join('src',item.test)],{cwd:scratch,encoding:'utf8'});const output=run.stdout+run.stderr;
 const assertionFailure=run.status!==0&&output.includes(item.pattern)&&(/ERR_ASSERTION|AssertionError/.test(output));
 results.push({mutation:item.name,source_sha256:createHash('sha256').update(source).digest('hex'),test:item.pattern,exit_code:run.status,assertionFailure,output});
 if(!assertionFailure)throw new Error('Mutation did not fail its intended behavior assertion: '+item.name+'\n'+output);
}finally{await rm(scratch,{recursive:true,force:true})}}
await mkdir(join(root,'test-results'),{recursive:true});await writeFile(join(root,'test-results/mutations.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results.map(({mutation,test,assertionFailure})=>({mutation,test,assertionFailure})),null,2));
