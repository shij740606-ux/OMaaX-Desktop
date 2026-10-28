// Real staged runtime + real stdio MCP transport; only synthetic local work.
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,readFileSync,writeFileSync,existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {randomUUID,createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const runtime=process.env.OMAAX_TEST_DSH_RUNTIME?path.resolve(process.env.OMAAX_TEST_DSH_RUNTIME):path.join(desktop,'dsh-runtime');
const entry=path.join(runtime,'node_modules/@deepseek-ai/dsh/lib/bin.js');
const runtimeRequire=createRequire(entry),clientFile=runtimeRequire.resolve('@deepseek-ai/dsh-mcp-client');
assert.ok(readFileSync(clientFile,'utf8').includes('OMAAX_MCP_TOOL_TIMEOUT_V1'),'Staged MCP timeout patch is required');
const base=path.resolve(desktop,'../validation-local/mcp-timeout-sdk');mkdirSync(base,{recursive:true});
const home=mkdtempSync(path.join(base,'home-')),workspace=path.join(home,'workspace');mkdirSync(workspace);
const fast=process.argv.includes('--fast'),sleep=ms=>new Promise(r=>setTimeout(r,ms));
const scenarios=[
 {id:'builtin_default',server:'biodsh_ssh',tool:'execute',args:{delayMs:fast?100:65000},expected:'success'},
 {id:'finite_one_second',server:'biodsh_ssh',tool:'execute',args:{delayMs:fast?100:65000,timeoutSeconds:1,ignoreCancellation:true},expected:fast?'success':'timeout'},
 {id:'environment_default',server:'biodsh_ssh',tool:'server_environment',args:{delayMs:fast?100:65000,action:'execute'},expected:'success'},
 {id:'zero_user_cancel',server:'biodsh_ssh',tool:'execute',args:{delayMs:65000,timeoutSeconds:0},cancelAfterMs:300,expected:'cancelled'},
 {id:'huge_user_cancel',server:'biodsh_ssh',tool:'execute',args:{delayMs:65000,timeoutSeconds:3000000},cancelAfterMs:350,expected:'cancelled'},
 {id:'external_explicit',server:'external_probe',tool:'execute',args:{delayMs:3000,timeoutSeconds:0},expected:'timeout'},
];
for(const s of scenarios)s.args.caseId=s.id;
const servers=[{name:'biodsh_ssh',timeout:1800000,tools:['execute','server_environment']},{name:'external_probe',timeout:150,tools:['execute']}];
for(const s of servers){s.events=path.join(home,s.name+'.jsonl');s.fixture=path.join(home,s.name+'.json');writeFileSync(s.fixture,JSON.stringify({events:s.events,tools:s.tools}));}
const resultFile=path.join(home,'result.json'),probe=path.join(home,'probe.mjs');
writeFileSync(probe,`
import {writeFileSync,readFileSync,existsSync} from 'node:fs';
export const inject=['tools','agents'];
export function apply(ctx){let busy=false;const scenarios=${JSON.stringify(scenarios)};const timer=setInterval(async()=>{
 if(busy||!existsSync(${JSON.stringify(path.join(home,'request.json'))})||!scenarios.every(s=>ctx.tools.get('mcp__'+s.server+'__'+s.tool)))return;busy=true;clearInterval(timer);
 try{const {sessionId}=JSON.parse(readFileSync(${JSON.stringify(path.join(home,'request.json'))},'utf8'));const agent=ctx.agents.get(sessionId);
 const results=await Promise.all(scenarios.map(async s=>{const control=new AbortController(),at=Date.now();const timer=s.cancelAfterMs?setTimeout(()=>control.abort(new Error('Synthetic user cancellation')),s.cancelAfterMs):undefined;let outcome;
 try{outcome=await ctx.tools.execute({agent,name:'mcp__'+s.server+'__'+s.tool,arguments:s.args,callId:'isolated-'+s.id,signal:control.signal});}catch(error){outcome={thrown:true,message:error.message,code:error.code};}finally{clearTimeout(timer);}return {id:s.id,elapsedMs:Date.now()-at,outcome};}));writeFileSync(${JSON.stringify(resultFile)},JSON.stringify({results},null,2));
 }catch(error){writeFileSync(${JSON.stringify(resultFile)},JSON.stringify({probeError:String(error)}));}},100);ctx.effect(()=>()=>clearInterval(timer));}
`);
const inserts=servers.map(s=>`    - id: mcp-${s.name}\n      name: ${JSON.stringify(pathToFileURL(clientFile).href)}\n      config:\n        serverName: ${s.name}\n        transport: stdio\n        command: ${JSON.stringify(process.execPath)}\n        args: [${JSON.stringify(path.join(desktop,'scripts/fixtures/mcp-timeout-server.mjs'))}, ${JSON.stringify(s.fixture)}]\n        toolCallTimeoutMs: ${s.timeout}\n        failOnStartupError: true`);
inserts.push(`    - id: timeout-probe\n      name: ${JSON.stringify(pathToFileURL(probe).href)}`);
writeFileSync(path.join(home,'cordis.patch.yml'),'- insert:\n'+inserts.join('\n')+'\n');
const child=spawn(process.execPath,['--expose-internals',entry,'web','--port','0','--no-open'],{cwd:workspace,windowsHide:true,env:{...process.env,DSH_HOME:home,DSH_TELEMETRY_DISABLED:'1',DEEPSEEK_API_KEY:'',OPENAI_API_KEY:'',ANTHROPIC_API_KEY:''}});
let log='';const collect=b=>{log+=b;writeFileSync(path.join(home,'sdk.log'),log);};child.stdout.on('data',collect);child.stderr.on('data',collect);
try{
 let deadline=Date.now()+30000;while(!/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/.test(log)&&Date.now()<deadline)await sleep(100);
 const url=log.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/)?.[0];assert.ok(url,'SDK startup failed: '+log);
 const auth=await fetch(url,{redirect:'manual'}),cookie=auth.headers.getSetCookie().map(s=>s.split(';')[0]).find(s=>s.startsWith('dsh-auth-'));assert.ok(cookie);
 const rpc=async(method,request)=>{const r=await fetch(new URL(url).origin+'/api/'+method,{method:'POST',headers:{'Content-Type':'application/json',Cookie:cookie},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),method,payload:{args:{request}}})});const result=(await r.json()).result;assert.equal(result?.ok,true,JSON.stringify(result));return result.value;};
 const ws=await rpc('workspace/create',{path:workspace}),session=await rpc('session/create',{workspaceId:ws.workspace?.workspaceId??ws.workspaceId});
 writeFileSync(path.join(home,'request.json'),JSON.stringify({sessionId:session.sessionId??session.session?.sessionId??session.id}));
 console.log(JSON.stringify({phase:'running',home,fast,scenarios:scenarios.length}));
 deadline=Date.now()+(fast?15000:85000);while(!existsSync(resultFile)&&Date.now()<deadline)await sleep(250);
 assert.ok(existsSync(resultFile),'SDK integration did not settle: '+log);const report=JSON.parse(readFileSync(resultFile,'utf8'));assert.ok(!report.probeError,report.probeError);await sleep(300);
 assert.ok(!log.includes('TimeoutOverflowWarning'),'No timer overflow is allowed');
 for(const result of report.results){const s=scenarios.find(s=>s.id===result.id),server=servers.find(server=>server.name===s.server);
  const events=readFileSync(server.events,'utf8').trim().split('\n').map(JSON.parse),dispatches=events.filter(e=>e.type==='dispatched'&&e.args.caseId===s.id);
  assert.equal(dispatches.length,1,'Never automatically resubmit '+s.id);assert.deepEqual(dispatches[0].args,s.args);result.events=events.filter(e=>e.id===dispatches[0].id);
  assert.equal(result.outcome.isError,s.expected!=='success',JSON.stringify(result));
  if(s.expected==='success')assert.equal(result.events.filter(e=>e.type==='completed').length,1);else assert.equal(result.events.filter(e=>e.type==='cancelled').length,1);
  if(s.expected==='cancelled')assert.ok(result.elapsedMs>=s.cancelAfterMs-20&&result.elapsedMs<2000);
  if(s.id==='external_explicit')assert.ok(result.elapsedMs>=120&&result.elapsedMs<2000,'External MCP keeps its timeout');
  if(!fast&&s.id==='finite_one_second'){assert.ok(result.elapsedMs>=60500&&result.elapsedMs<64500);assert.equal(result.events.filter(e=>e.type==='completed').length,1);}
  if(!fast&&s.expected==='success')assert.ok(result.elapsedMs>=64500&&result.elapsedMs<75000);
 }
 const output={passed:report.results.length,fast,runtimeVersion:JSON.parse(readFileSync(path.join(runtime,'node_modules/@deepseek-ai/dsh/package.json'))).version,home,clientFile,clientSha256:createHash('sha256').update(readFileSync(clientFile)).digest('hex'),boundary:'Real staged SDK, agent and stdio transport; synthetic local tools only, no SSH, model or account access.',...report};
 writeFileSync(path.join(base,fast?'fast-result.json':'integration-result.json'),JSON.stringify(output,null,2));console.log(JSON.stringify({passed:output.passed,home,fast}));
}finally{child.kill();await sleep(500);for(const s of servers){if(!existsSync(s.events))continue;const pid=readFileSync(s.events,'utf8').trim().split('\n').map(JSON.parse).find(e=>e.type==='started')?.pid;if(pid){try{process.kill(pid);}catch(error){if(error.code!=='ESRCH')throw error;}}}}
