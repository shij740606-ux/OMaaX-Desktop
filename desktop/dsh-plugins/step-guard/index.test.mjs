import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,unlinkSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {apply} from './index.js';
import {createGuard} from '../workflow-guard/index.js';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../../validation-local/step-guard-tests');mkdirSync(root,{recursive:true});
function fixture(){
 const directory=mkdtempSync(path.join(root,'case-'));process.env.OMAAX_WORKFLOW_GUARD_DIR=directory;
 let handler;apply({on:(name,fn)=>{assert.equal(name,'agent/pre-step');handler=fn;}});
 const agent={id:'workflow-session'},file=path.join(directory,agent.id+'.json');
 const bind=()=>writeFileSync(file,JSON.stringify({schema:1,sessionId:agent.id,workflow:'rnaseq',version:'test'}));
 const step=(step,turn=1)=>handler({agent,step,turn},async()=>({kind:'enter',messages:[]}));
 return {directory,agent,file,bind,step};
}
test('bound workflows continue past ordinary step and time budgets, including resumed agents',async()=>{
 const f=fixture();f.bind();const clock=Date.now;
 try {
  assert.equal((await f.step(1)).messages.length,0);Date.now=()=>clock()+3600000;
  for(const n of [12,20,80,200])assert.equal((await f.step(n)).messages.length,0);
  assert.equal((await f.step(200,2)).messages.length,0);
  const guard=createGuard(f.directory);unlinkSync(f.file);
  assert.equal((await f.step(201)).messages.length,0);
  f.bind();assert.ok(guard({agent:f.agent,name:'mcp__biodsh_ssh__server_delete_confirm'}));
  assert.ok(guard({agent:f.agent,name:'bash'}));
  assert.equal(guard({agent:f.agent,name:'mcp__biodsh_ssh__workflow_prepare'}),undefined);
 } finally {Date.now=clock;}
});
test('ordinary chats retain budget reminders and late workflow binding takes effect',async()=>{
 const f=fixture();assert.ok((await f.step(200)).messages.some(m=>m.content[0].text.includes('不要再调用任何工具')));
 f.bind();assert.equal((await f.step(201)).messages.length,0);
});
test('invalid workflow binding cannot disable reminders or release protected tools',async()=>{
 const f=fixture();writeFileSync(f.file,'broken');assert.ok((await f.step(200)).messages.length);
 assert.ok(createGuard(f.directory)({agent:f.agent,name:'mcp__biodsh_ssh__workflow_prepare'}));
});
