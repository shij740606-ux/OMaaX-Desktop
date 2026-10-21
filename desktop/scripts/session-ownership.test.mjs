// Real bundled DSH, disposable data, no model requests or user conversations.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,readFileSync,readdirSync,rmSync,writeFileSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {once} from 'node:events';

test('concurrent cold resume, crash recovery and repeated restart preserve one durable session', {timeout:120000},async()=>{
 const home=mkdtempSync(path.join(tmpdir(),'omaax-session-'));
 const ws=path.join(home,'workspace');mkdirSync(ws);
 const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
 const bin=path.join(root,'dsh-runtime/node_modules/@deepseek-ai/dsh/lib/bin.js');
 const server=createServer((req,res)=>{req.resume();req.on('end',()=>{
  res.writeHead(200,{'Content-Type':'text/event-stream'});
  res.end('data: '+JSON.stringify({id:'test',object:'chat.completion.chunk',choices:[{index:0,delta:{role:'assistant',content:'Preserved test answer'},finish_reason:null}]})+'\n\ndata: '+JSON.stringify({id:'test',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}})+'\n\ndata: [DONE]\n\n');
 });});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 writeFileSync(path.join(home,'cordis.patch.yml'),`- id: llm-deepseek\n  config:\n    baseURL: http://127.0.0.1:${server.address().port}/v1\n    apiKeyEnv: OMAAX_TEST_KEY\n`);
 let child,logs='';
 async function stop(){if(child&&child.exitCode===null){const done=once(child,'exit');child.kill();await done;}child=null;}
 async function start(){
  logs='';child=spawn(process.execPath,['--expose-internals',bin,'web','--port','0','--no-open'],{cwd:ws,env:{...process.env,DSH_HOME:home,DSH_TELEMETRY_DISABLED:'1',OMAAX_TEST_KEY:'fixture-only'},windowsHide:true});
  const tokenUrl=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(new Error('DSH startup timed out')),30000);
   const scan=b=>{logs+=b;const m=logs.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/);if(m){clearTimeout(timer);resolve(m[0]);}};
   child.stdout.on('data',scan);child.stderr.on('data',scan);child.once('exit',c=>{clearTimeout(timer);reject(new Error(`DSH exited ${c}`));});
  });
  const origin=new URL(tokenUrl).origin;
  const auth=await fetch(tokenUrl,{redirect:'manual'});
  const cookie=auth.headers.getSetCookie().map(s=>s.split(';')[0]).find(s=>s.startsWith('dsh-auth-'));
  assert.ok(cookie);
  return async(method,wire,args)=>{
   const response=await fetch(`${origin}/api/${method}`,{method:'POST',headers:{'Content-Type':'application/json',Cookie:cookie},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),method,payload:{args:{[wire]:args}}}),signal:AbortSignal.timeout(20000)});
   const result=(await response.json()).result;assert.equal(result?.ok,true,JSON.stringify(result));return result.value;
  };
 }
 try{
  let rpc=await start();const workspace=await rpc('workspace/create','request',{path:ws});
  const created=await rpc('session/create','request',{workspaceId:workspace.workspace?.workspaceId??workspace.workspaceId});
  const sid=created.sessionId??created.session?.sessionId??created.id;assert.ok(sid);
  await rpc('session/rename','request',{sessionId:sid,title:'preserved title'});
  await rpc('session/prompt','request',{sessionId:sid,mode:'queue',requestId:randomUUID(),content:[{type:'text',text:'Preserve this test conversation'}]});
  await new Promise(resolve=>setTimeout(resolve,2000));
  for(let round=0;round<3;round++){
   await stop();
   const sessionRoot=path.join(home,'sessions');
   const files=readdirSync(sessionRoot,{recursive:true}).filter(p=>p.endsWith('.jsonl') || p.endsWith('.zstd'));
   const before=new Map(files.map(p=>[p,readFileSync(path.join(sessionRoot,p))]));assert.ok(before.size,'no durable session written');
   rpc=await start();
   const list=await rpc('session/list','_request',{});assert.ok(JSON.stringify(list).includes(sid));
   await Promise.all(Array.from({length:20},(_,i)=>rpc('session/rename','request',{sessionId:sid,title:`round ${round} rename ${i}`})));
   for(const [p,bytes] of before){const after=readFileSync(path.join(sessionRoot,p));assert.deepEqual(after.subarray(0,bytes.length),bytes,'history prefix changed');}
   assert.doesNotMatch(logs,/SessionAlreadyOwnedError|resume failed/);
   await new Promise(resolve=>setTimeout(resolve,500));
  }
 }finally{await stop();server.close();rmSync(home,{recursive:true,force:true});}
});
