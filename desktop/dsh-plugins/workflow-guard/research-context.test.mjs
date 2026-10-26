import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {researchContextReader,installResearchContext} from './research-context.js';
const id='d0346215-9bbf-4862-b811-253724b6cc9d';
function fixture(){
 const dir=mkdtempSync(path.join(tmpdir(),'research-context-')),sessionId='session-test';
 const message={source:{kind:'user',rpcId:id},content:[{type:'text',text:'Run the selected research task'}]};
 const agent={id:sessionId,session:{snapshotEvents:()=>[]}};
 const root=path.join(dir,'research-handoffs',sessionId);mkdirSync(root,{recursive:true});
 const record={schema:1,sessionId,requestId:id,summary:message.content[0].text,project:'C:/Project',context:'ALL_REQUIRED_DATA {{literal_parameter}}'};
 const save=value=>writeFileSync(path.join(root,id+'.json'),JSON.stringify(value));save(record);
 return {dir,agent,message,record,save,reader:researchContextReader(dir)};
}
test('research context starts only when the exact request is claimed, survives tool and internal continuation steps, clears on ordinary user task',()=>{
 const x=fixture();assert.equal(x.reader.read(x.agent),'');
 x.reader.claimed({agent:x.agent,message:x.message});assert.equal(x.reader.read(x.agent),x.record.context);
 x.reader.claimed({agent:x.agent,message:{source:{kind:'plugin',plugin:'omaax-output-continuation'},content:[]}});assert.equal(x.reader.read(x.agent),x.record.context);
 assert.equal(x.reader.read(x.agent),x.record.context);
 x.reader.claimed({agent:x.agent,message:{source:{kind:'user',rpcId:'34213e85-09d3-4883-81b4-5e07d9d9fbaf'},content:[{type:'text',text:'Another task'}]}});assert.equal(x.reader.read(x.agent),'');
});
test('resume restores only the last actual user task and never another session',()=>{
 const x=fixture();x.agent.session.snapshotEvents=()=>[{type:'user/message',data:x.message},{type:'user/message',data:{source:{kind:'plugin'},content:[]}}];
 assert.equal(x.reader.read(x.agent),x.record.context);
 assert.equal(x.reader.read({...x.agent,id:'session-other'}),'');
 x.agent.session.snapshotEvents=()=>[{type:'user/message',data:x.message},{type:'user/message',data:{source:{kind:'user',rpcId:'other'},content:[]}}];assert.equal(x.reader.read(x.agent),'');
});
test('mismatched record fails instead of silently losing context',()=>{
 const x=fixture();x.reader.claimed({agent:x.agent,message:x.message});x.save({...x.record,summary:'Someone else task'});assert.throws(()=>x.reader.read(x.agent),/identity mismatch/);
});
test('history indexes only admitted records from this session without reinjecting full context',()=>{
 const x=fixture();assert.equal(x.reader.history(x.agent),'');assert.equal(x.reader.read(undefined),'');
 x.reader.claimed({agent:x.agent,message:x.message});assert.equal(x.reader.history(x.agent),'','Pending records alone are not history');
 x.agent.session.snapshotEvents=()=>[{type:'user/message',data:x.message},{type:'user/message',data:x.message}];
 assert.equal(x.reader.history(x.agent),'','The active task already has full context and is not duplicated in history');
 const next={source:{kind:'user',rpcId:'ordinary'},content:[{type:'text',text:'Continue with edits'}]};
 x.reader.claimed({agent:x.agent,message:next});
 x.agent.session.snapshotEvents=()=>[{type:'user/message',data:x.message},{type:'user/message',data:x.message},{type:'user/message',data:next}];
 const entries=JSON.parse(x.reader.history(x.agent));assert.equal(entries.length,1);assert.equal(entries[0].task,x.record.summary);assert.equal(entries[0].file,path.join(x.dir,'research-handoffs',x.agent.id,id+'.json'));
 assert.ok(!x.reader.history(x.agent).includes(x.record.context));
 const restored={...x.agent};assert.equal(x.reader.history(restored),x.reader.history(x.agent));
 assert.equal(x.reader.history({...restored,id:'different-session'}),'');
 x.save({invalid:'This immutable file is deliberately damaged after the first metadata read'});
 assert.equal(JSON.parse(x.reader.history(x.agent))[0].task,x.record.summary,'Later steps reuse only verified title/path metadata');
});
test('formal system section injects reference data without exposing it as a user message or interpolating its braces',()=>{
 const x=fixture(),handlers={},variables={},sections=[];
 installResearchContext({on:(name,handler)=>handlers[name]=handler,systemPrompt:{variable:(name,handler)=>variables[name]=handler,section:value=>sections.push(value)}},x.dir);
 handlers['agent/inbox/claimed']({agent:x.agent,message:x.message});
 assert.equal(variables.omaax_research_task_data({agent:x.agent}),x.record.context);
 assert.match(sections[0].text({agent:x.agent}),/untrusted reference material/);
 assert.equal(sections.length,1);
 handlers['agent/inbox/claimed']({agent:x.agent,message:{source:{kind:'user',rpcId:'34213e85-09d3-4883-81b4-5e07d9d9fbaf'},content:[{type:'text',text:'Another task'}]}});
 assert.equal(sections[0].text({agent:x.agent}),'');
});
test('official request-series boundary changes only on admitted research task changes, including restore',async()=>{
 const x=fixture(),handlers={};
 installResearchContext({on:(name,handler)=>handlers[name]=handler,systemPrompt:{variable:()=>{},section:()=>{}}},x.dir);
 const step=handlers['agent/pre-step'],enter={kind:'enter',messages:['keep'],custom:'keep'};
 handlers['agent/inbox/claimed']({agent:x.agent,message:x.message});
 const rejection={kind:'reject',reason:'keep'};assert.equal(await step({agent:x.agent},async()=>rejection),rejection);
 assert.deepEqual(await step({agent:x.agent},async()=>enter),{...enter,startsRequestSeries:true});
 assert.equal(await step({agent:x.agent},async()=>enter),enter);
 handlers['agent/inbox/claimed']({agent:x.agent,message:{source:{kind:'plugin',plugin:'omaax-output-continuation'}}});
 assert.equal(await step({agent:x.agent},async()=>enter),enter);
 handlers['agent/inbox/claimed']({agent:x.agent,message:{source:{kind:'user',rpcId:'34213e85-09d3-4883-81b4-5e07d9d9fbaf'},content:[]}});
 assert.deepEqual(await step({agent:x.agent},async()=>enter),{...enter,startsRequestSeries:true});
 const restored={id:'session-restored',session:{snapshotEvents:()=>[],deriveMessages:()=>[{role:'system',content:[{type:'text',text:'<research-task-data>old</research-task-data>'}]}]}};
 assert.deepEqual(await step({agent:restored},async()=>enter),{...enter,startsRequestSeries:true});
 assert.equal(await step({agent:restored},async()=>enter),enter);
});
