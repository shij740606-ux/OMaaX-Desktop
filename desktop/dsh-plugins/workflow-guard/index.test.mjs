import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdirSync,mkdtempSync,writeFileSync,readFileSync,unlinkSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createGuard,apply,bindingReader,selectionContext} from './index.js';
const fixtures=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../../deploy/local/fixed-workflows-20261013/guard-tests');
mkdirSync(fixtures,{recursive:true});
function bound() {
 const dir=mkdtempSync(path.join(fixtures,'policy-'));const agent={id:'workflow-session'};
 const file=path.join(dir,agent.id+'.json');
 writeFileSync(file,JSON.stringify({schema:1,sessionId:agent.id,workflow:'rnaseq',version:'20261013.1'}));
 return {dir,agent,file,guard:createGuard(dir)};
}
test('shell, filesystem, arbitrary MCP, delegate and PTC are denied before execution',()=>{
 const {agent,guard}=bound();
 for(const name of ['bash','read_file','write_file','run_code','delegate','mcp__other__execute','mcp__biodsh_ssh__submit_task','mcp__biodsh_ssh__execute'])assert.ok(guard({agent,name,arguments:{}}));
});
test('only declared fixed workflow tools are accepted',()=>{
 const {agent,guard}=bound();
 assert.equal(guard({agent,name:'mcp__biodsh_ssh__workflow_plan',arguments:{workflow:'rnaseq'}}),undefined);
 assert.ok(guard({agent,name:'mcp__biodsh_ssh__workflow_plan',arguments:{workflow:'spatial'}}));
 assert.equal(guard({agent,name:'mcp__biodsh_ssh__task_logs',arguments:{}}),undefined);
});
test('page switches or removing a policy cannot release a live workflow session',()=>{
 const {agent,guard,file}=bound();
 assert.ok(guard({agent,name:'bash'}));unlinkSync(file); // Only this test's own temporary policy file.
 assert.ok(guard({agent,name:'bash'}));
});
test('corrupt policy fails closed and unrelated conversations retain their tools',()=>{
 const {dir,file,guard}=bound();
 writeFileSync(file,'broken');
 assert.ok(guard({agent:{id:'workflow-session'},name:'bash'}));
 assert.equal(guard({agent:{id:'ordinary-session'},name:'bash'}),undefined);
});
test('plugin requires the monotonic SDK guard and emits a readiness heartbeat',()=>{
 assert.throws(()=>apply({tools:{}}));
 const dir=mkdtempSync(path.join(fixtures,'plugin-'));const previous=process.env.OMAAX_WORKFLOW_GUARD_DIR;
 process.env.OMAAX_WORKFLOW_GUARD_DIR=dir;let registered,cleanup;
 try {apply({on:()=>{},tools:{guard:g=>{registered=g;}},systemPrompt:{section:()=>{},variable:()=>{}},effect:f=>{cleanup=f();}});assert.equal(typeof registered,'function');const ready=JSON.parse(readFileSync(path.join(dir,'ready.json'),'utf8'));assert.equal(ready.schema,1);assert.equal(ready.selectionContext,true);assert.ok(Date.now()-ready.at<1000);}
 finally{cleanup?.();if(previous===undefined)delete process.env.OMAAX_WORKFLOW_GUARD_DIR;else process.env.OMAAX_WORKFLOW_GUARD_DIR=previous;}
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
 for(const name of ['workflow_prepare','workflow_prepare_status','workflow_readiness','workflow_environment'])assert.equal(guard({agent,name:'mcp__biodsh_ssh__'+name,arguments:{workflow:'rnaseq'}}),undefined);
 for(const name of ['server_delete_confirm','execute'])assert.ok(guard({agent,name:'mcp__biodsh_ssh__'+name}));
});
