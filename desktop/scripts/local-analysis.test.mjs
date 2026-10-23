import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {once} from 'node:events';

test('real bundled harness runs local analysis with account and selected-server workflow guards', {timeout:90000,skip:process.platform!=='win32'},async()=>{
 const home=mkdtempSync(path.join(tmpdir(),'omaax-local-harness-')),ws=path.join(home,'workspace');mkdirSync(ws);
 const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
 const input=path.join(ws,'input.csv');writeFileSync(input,'Value\n1\n2\n3\n');const original=readFileSync(input);
 const plugin=path.join(home,'fixture.mjs');
 writeFileSync(plugin,`import {createGuard as account} from ${JSON.stringify(pathToFileURL(path.join(root,'dsh-plugins/auth-guard/index.js')).href)};\nimport {createGuard as workflow} from ${JSON.stringify(pathToFileURL(path.join(root,'dsh-plugins/workflow-guard/index.js')).href)};\nexport const inject=['tools'];export function apply(ctx){ctx.tools.guard(account(()=>({ok:true})));ctx.tools.guard(workflow('',()=>({workflow:'rnaseq',version:'test',selection:{serverId:'selected-server'}})));}`);
 let toolResult,seenTools=[],requests=0,child,logs='';
 const server=createServer((req,res)=>{let body='';req.on('data',b=>body+=b);req.on('end',()=>{
  const payload=JSON.parse(body);requests++;seenTools=payload.tools?.map(t=>t.function.name)||[];
  toolResult=payload.messages?.findLast(m=>m.role==='tool')?.content;
  const delta=toolResult===undefined?{role:'assistant',tool_calls:[{index:0,id:'local-csv',type:'function',function:{name:'pwsh',arguments:JSON.stringify({command:"Import-Csv './input.csv' | Measure-Object -Property Value -Sum | Select-Object Sum | ConvertTo-Json -Compress",description:'Calculate local fixture sum',workdir:ws})}}]}:{role:'assistant',content:'Local result verified'};
  res.writeHead(200,{'Content-Type':'text/event-stream'});res.end('data: '+JSON.stringify({id:'fixture',choices:[{index:0,delta,finish_reason:null}]})+'\n\ndata: '+JSON.stringify({id:'fixture',choices:[{index:0,delta:{},finish_reason:toolResult===undefined?'tool_calls':'stop'}]})+'\n\ndata: [DONE]\n\n');
 });});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 writeFileSync(path.join(home,'cordis.patch.yml'),`- id: llm-deepseek\n  disabled: true\n- id: llm-pi-ai\n  config:\n    providers:\n      deepseek-official:\n        api: openai-completions\n        baseURL: http://127.0.0.1:${server.address().port}/v1\n        apiKeyEnv: OMAAX_TEST_KEY\n        models:\n          - id: omaax\n            reasoningEfforts: false\n- id: agent-default-model\n  config:\n    provider: deepseek-official\n    model: omaax\n- insert:\n    - id: local-fixture-guards\n      name: ${JSON.stringify(pathToFileURL(plugin).href)}\n`);
 try{
  child=spawn(process.execPath,['--expose-internals',path.join(root,'dsh-runtime/node_modules/@deepseek-ai/dsh/lib/bin.js'),'web','--port','0','--no-open'],{cwd:ws,env:{...process.env,DSH_HOME:home,DSH_TELEMETRY_DISABLED:'1',OMAAX_TEST_KEY:'fixture-only'},windowsHide:true});
  const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout '+logs.slice(-2000))),30000);const scan=b=>{logs+=b;const m=logs.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/);if(m){clearTimeout(timer);resolve(m[0]);}};child.stdout.on('data',scan);child.stderr.on('data',scan);child.once('exit',()=>{clearTimeout(timer);reject(Error(logs));});});
  const auth=await fetch(url,{redirect:'manual'}),cookie=auth.headers.getSetCookie().map(s=>s.split(';')[0]).find(s=>s.startsWith('dsh-auth-'));
  const rpc=async(method,args)=>{const r=await fetch(new URL('/api/'+method,url),{method:'POST',headers:{'Content-Type':'application/json',Cookie:cookie},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),method,payload:{args:{request:args}}})});const value=(await r.json()).result;assert.equal(value?.ok,true,JSON.stringify(value));return value.value;};
  const w=await rpc('workspace/create',{path:ws});const s=await rpc('session/create',{workspaceId:w.workspace?.workspaceId??w.workspaceId});
  await rpc('session/prompt',{sessionId:s.sessionId??s.session?.sessionId??s.id,mode:'queue',requestId:randomUUID(),content:[{type:'text',text:'Calculate sum of Value in the local input.csv using PowerShell. Do not use the selected server.'}]});
  for(let i=0;i<100&&toolResult===undefined;i++)await new Promise(r=>setTimeout(r,300));
  assert.ok(seenTools.includes('pwsh'));assert.ok(requests>=2,'harness did not return a local tool result: '+logs.slice(-2000));assert.match(JSON.stringify(toolResult),/Sum.*6/);assert.doesNotMatch(JSON.stringify(toolResult),/账号保护|一体化分析流程对话/);assert.deepEqual(readFileSync(input),original);
 }finally{if(child&&child.exitCode===null){const done=once(child,'exit');child.kill();await done;}server.close();rmSync(home,{recursive:true,force:true});}
});
