import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync,mkdtempSync,writeFileSync,readFileSync,unlinkSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createGuard,apply,bindingReader,selectionContext,recordTaskIntent} from './index.js';
const fixtures=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../../deploy/local/fixed-workflows-20261013/guard-tests');
mkdirSync(fixtures,{recursive:true});
test('task intent comes only from actual user events and explicit stop supersedes execution',()=>{
 const dir=mkdtempSync(path.join(fixtures,'intent-')),session={id:'test'};
 const event=(seq,text,kind='user')=>({type:'user/message',seq,data:{source:{kind},content:[{type:'text',text}]}});
 recordTaskIntent(dir,session,event(1,'开始分析这批数据'));
 const file=path.join(dir,'intent-test.json');
 assert.equal(JSON.parse(readFileSync(file)).executionRequested,true);
 recordTaskIntent(dir,session,event(2,'先不要运行，先讨论分析方案'));
 assert.equal(JSON.parse(readFileSync(file)).executionRequested,false);
 recordTaskIntent(dir,session,event(3,'开始执行','tool'));
 recordTaskIntent(dir,session,event(1,'开始执行'));
 assert.equal(JSON.parse(readFileSync(file)).executionRequested,false);
 recordTaskIntent(dir,session,event(4,'按方案继续执行'));
 assert.equal(JSON.parse(readFileSync(file)).executionRequested,true);
 recordTaskIntent(dir,session,event(5,'给我一个分析方案'));
 assert.equal(JSON.parse(readFileSync(file)).executionRequested,false);
});
function bound() {
 const dir=mkdtempSync(path.join(fixtures,'policy-'));const agent={id:'workflow-session'};
 const file=path.join(dir,agent.id+'.json');
 writeFileSync(file,JSON.stringify({schema:1,sessionId:agent.id,workflow:'rnaseq',version:'20261013.1'}));
 return {dir,agent,file,guard:createGuard(dir)};
}
test('shell, filesystem, arbitrary MCP, delegate and PTC are denied before execution',()=>{
 const {agent,guard}=bound();
 for(const name of ['read_file','write_file','run_code','delegate','mcp__other__execute','mcp__biodsh_ssh__submit_task','mcp__biodsh_ssh__execute'])assert.ok(guard({agent,name,arguments:{}}));
});
test('only declared fixed workflow tools are accepted',()=>{
 const {agent,guard}=bound();
 assert.equal(guard({agent,name:'mcp__biodsh_ssh__workflow_plan',arguments:{workflow:'rnaseq'}}),undefined);
 assert.ok(guard({agent,name:'mcp__biodsh_ssh__workflow_plan',arguments:{workflow:'spatial'}}));
 assert.equal(guard({agent,name:'mcp__biodsh_ssh__task_logs',arguments:{}}),undefined);
});
test('page switches or removing a policy cannot release a live workflow session',()=>{
 const {agent,guard,file}=bound();
 const wrong={agent,name:'mcp__biodsh_ssh__workflow_plan',arguments:{workflow:'spatial'}};
 assert.ok(guard(wrong));unlinkSync(file); // Only this test's own temporary policy file.
 assert.ok(guard(wrong));
 assert.equal(guard({agent,name:'bash'}),undefined);
});
test('corrupt policy fails closed and unrelated conversations retain their tools',()=>{
 const {dir,file,guard}=bound();
 writeFileSync(file,'broken');
 assert.equal(guard({agent:{id:'workflow-session'},name:'bash'}),undefined);
 assert.ok(guard({agent:{id:'workflow-session'},name:'mcp__biodsh_ssh__workflow_plan',arguments:{workflow:'rnaseq'}}));
 assert.equal(guard({agent:{id:'ordinary-session'},name:'bash'}),undefined);
});
test('plugin requires the monotonic SDK guard and emits a readiness heartbeat',()=>{
 assert.throws(()=>apply({tools:{}}));
 const dir=mkdtempSync(path.join(fixtures,'plugin-'));const previous=process.env.OMAAX_WORKFLOW_GUARD_DIR;
 process.env.OMAAX_WORKFLOW_GUARD_DIR=dir;let registered,cleanup;
 try {apply({on:()=>{},tools:{guard:g=>{registered=g;}},systemPrompt:{section:()=>{},variable:()=>{}},effect:f=>{cleanup=f();}});assert.equal(typeof registered,'function');const ready=JSON.parse(readFileSync(path.join(dir,'ready.json'),'utf8'));assert.equal(ready.schema,1);assert.equal(ready.selectionContext,true);assert.ok(Date.now()-ready.at<1000);}
 finally{cleanup?.();if(previous===undefined)delete process.env.OMAAX_WORKFLOW_GUARD_DIR;else process.env.OMAAX_WORKFLOW_GUARD_DIR=previous;}
});
test('past failures do not lock environment repair, input configuration or fixed planning',()=>{
 const {dir,agent}=bound(),previous=process.env.OMAAX_WORKFLOW_GUARD_DIR,guards=[];let cleanup;
 writeFileSync(path.join(dir,'budget-'+agent.id+'.json'),JSON.stringify({failures:8}));
 process.env.OMAAX_WORKFLOW_GUARD_DIR=dir;
 try {
  apply({on:()=>{},tools:{guard:g=>guards.push(g)},systemPrompt:{section:()=>{},variable:()=>{}},effect:f=>{cleanup=f();}});
  for(const name of ['workflow_prepare','workflow_environment','workflow_plan','workspace_config','server_files','server_write','workspace_analysis','workspace_index'])
   assert.equal(guards.map(g=>g({agent,name:'mcp__biodsh_ssh__'+name,arguments:{workflow:'rnaseq'}})).find(Boolean),undefined,name);
  assert.ok(guards.map(g=>g({agent,name:'mcp__biodsh_ssh__server_delete_confirm',arguments:{}})).find(Boolean));
 } finally {cleanup?.();if(previous===undefined)delete process.env.OMAAX_WORKFLOW_GUARD_DIR;else process.env.OMAAX_WORKFLOW_GUARD_DIR=previous;}
});
test('all nine selections survive resume, keep exact parameters and never bleed into another conversation',()=>{
 for(const workflow of ['gene_family','rnaseq','singlecell','spatial','metagenome','atacseq','chipseq','cutandrun','methylseq']){
  const {dir,agent,file}=bound(),selection={workflow,version:'20261014.1',serverId:'175',source:'shared',input:'small-test',steps:['step'],parameters:{step:{label:'中文参数',cut_front:false,padj:0.05}},configs:{step:{samples:['a','b']}},cpu:3,memoryMb:4096,timeoutSeconds:3600};
  const saved={schema:1,sessionId:agent.id,workflow,version:selection.version,selection};writeFileSync(file,JSON.stringify(saved));
  assert.equal(bindingReader(dir)(agent).selection.parameters.step.cut_front,false);
  assert.deepEqual(bindingReader(dir)({id:agent.id}).selection,selection);
  assert.ok(selectionContext(saved).includes(JSON.stringify(selection)));
  assert.equal(selectionContext(bindingReader(dir)({id:'ordinary'})),'');
  const guard=createGuard(dir);
  assert.equal(guard({agent,name:'mcp__biodsh_ssh__workflow_inspect',arguments:{serverId:'175',source:'shared',input:'small-test'}}),undefined);
  assert.equal(guard({agent,name:'mcp__biodsh_ssh__workflow_inspect',arguments:{serverId:'175',source:'shared',input:'./small-test/reads'}}),undefined);
  assert.equal(guard({agent,name:'mcp__biodsh_ssh__workflow_inspect',arguments:{serverId:'176',input:'/home/account/data'}}),undefined);
  for(const input of ['/home/account/data','/srv/references','~/data'])assert.equal(guard({agent,name:'mcp__biodsh_ssh__workflow_plan',arguments:{serverId:'175',workflow,input}}),undefined);
  for(const tool of ['server_files','workspace_config','workspace_index','workspace_script','workspace_analysis','workspace_files','workspace_restore'])assert.equal(guard({agent,name:'mcp__biodsh_ssh__'+tool,arguments:{serverId:'176'}}),undefined);
 }
});

test('model environment tools are allowed while delete approval and host execution remain blocked',()=>{
 const {dir,agent}=bound(),guard=createGuard(dir);
 for(const name of ['server_environment','workflow_prepare','workflow_prepare_status','workflow_readiness','workflow_environment'])assert.equal(guard({agent,name:'mcp__biodsh_ssh__'+name,arguments:{workflow:'rnaseq'}}),undefined);
 for(const name of ['server_delete_confirm','workflow_resume','execute'])assert.ok(guard({agent,name:'mcp__biodsh_ssh__'+name}));
});

test('workflow and account guards allow research but retain execution and deletion boundaries',async()=>{
 const {createGuard:accountGuard}=await import('../auth-guard/index.js');
 const {agent,guard}=bound(),account=accountGuard(()=>({ok:true}));
 for(const name of ['read','grep','glob','read_image','web_fetch','web_search','skill','ask_user_question','pwsh','bash','write','edit','job_list','job_output','job_kill','todo_write','present','omaax_local_delete']){
  const exec={agent,name,arguments:{}};assert.equal(guard(exec),undefined);assert.equal(account(exec),undefined);
  assert.match(accountGuard(()=>({ok:false}))(exec),/账号未登录/);
 }
 for(const name of ['subagent','mcp__biodsh_ssh__server_delete_confirm','mcp__biodsh_ssh__server_write_confirm']){
  const exec={agent,name,arguments:{}};assert.ok(guard(exec));assert.ok(account(exec));
 }
 const install={agent,name:'mcp__biodsh_ssh__workflow_prepare',arguments:{workflow:'rnaseq',recipes:{rnaseq_de:{packages:['r-base=4.4','r-biocmanager'],channels:['conda-forge','bioconda'],script:'Rscript -e "library(BiocManager)"'}}}};
 assert.equal(guard(install),undefined);assert.equal(account(install),undefined);
 assert.ok(guard({agent,name:'mcp__biodsh_ssh__workflow_plan',arguments:{workflow:'gene_family'}}));
});

test('task execution survives input answers, status questions and synthetic reminders; explicit stop wins',()=>{
 const dir=mkdtempSync(path.join(fixtures,'intent-')),session={id:'persisted'};
 const event=(seq,text)=>({type:'user/message',seq,data:{source:{kind:'user'},content:text}});
 const read=()=>JSON.parse(readFileSync(path.join(dir,'intent-persisted.json'),'utf8'));
 recordTaskIntent(dir,session,event(1,'开始分析'));
 for(const [seq,text] of [[2,'/data/reads'],[3,'reverse'],[4,'现在怎么样了？']]){
  recordTaskIntent(dir,session,event(seq,text));assert.equal(read().executionRequested,true);
 }
 recordTaskIntent(dir,session,event(5,'【系统提醒】请停止继续探索'));
 assert.equal(read().seq,4);assert.equal(read().executionRequested,true);
 recordTaskIntent(dir,session,event(6,'暂停'));assert.equal(read().executionRequested,false);
 recordTaskIntent(dir,session,event(7,'/data/other'));assert.equal(read().executionRequested,false);
 recordTaskIntent(dir,session,event(8,'确认'));assert.equal(read().executionRequested,true);
 recordTaskIntent(dir,{id:'another'},event(1,'reverse'));
 assert.equal(JSON.parse(readFileSync(path.join(dir,'intent-another.json'))).executionRequested,false);
});
