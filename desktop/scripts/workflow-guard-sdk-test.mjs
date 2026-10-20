import {spawn} from 'node:child_process';
import {mkdirSync,mkdtempSync,writeFileSync,readFileSync,existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import assert from 'node:assert/strict';
const desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const base=path.resolve(desktop,'../deploy/local/fixed-workflows-20261013/sdk-tests');mkdirSync(base,{recursive:true});
const home=mkdtempSync(path.join(base,'home-'));const bindings=path.join(home,'workflow-bindings');mkdirSync(bindings);
const sdk=path.join(desktop,'dsh-runtime/node_modules/@deepseek-ai/dsh');
const fixture=path.join(home,'probe.mjs');
writeFileSync(fixture,[
 "import {defineContentToolFixture} from "+JSON.stringify(pathToFileURL(path.join(sdk,'node_modules/@deepseek-ai/dsh-tools/lib/index.js')).href)+";",
 "import {readFileSync,writeFileSync,existsSync} from 'node:fs';",
 "import path from 'node:path';",
 "import {renderPrompt} from "+JSON.stringify(pathToFileURL(path.join(sdk,'node_modules/@deepseek-ai/dsh-system-prompt/lib/index.js')).href)+";",
 "import {agentEvents} from "+JSON.stringify(pathToFileURL(path.join(sdk,'node_modules/@deepseek-ai/dsh-agent/lib/index.js')).href)+";",
 "import {createGuard} from "+JSON.stringify(pathToFileURL(path.join(desktop,'dsh-plugins/auth-guard/index.js')).href)+";",
 "export const inject=['tools','agents','systemPrompt'];",
 "export function apply(ctx) {",
 " let bodies=0;",
 " ctx.tools.register(defineContentToolFixture({name:'workflow_probe_forbidden',description:'Harmless test marker',parameters:{},execute:async()=>{bodies++;return [{type:'text',text:'FORBIDDEN_BODY'}];}}));",
 " ctx.tools.register(defineContentToolFixture({name:'mcp__biodsh_ssh__workflow_catalog',description:'Harmless allowed marker',parameters:{},execute:async()=>[{type:'text',text:'ALLOWED_BODY'}]}));",
 " ctx.tools.register(defineContentToolFixture({name:'mcp__biodsh_ssh__server_write',description:'Identity test only, writes nothing',parameters:{},execute:async args=>[{type:'text',text:JSON.stringify(args)}]}));",
 " for(const name of ['workflow_readiness','workflow_prepare','workflow_prepare_status','workflow_environment','server_delete_confirm'])ctx.tools.register(defineContentToolFixture({name:'mcp__biodsh_ssh__'+name,description:'Local permission probe',parameters:{},execute:async()=>{if(name==='server_delete_confirm')bodies++;return [{type:'text',text:'ENV_ALLOWED_'+name}];}}));",
 " ctx.on('tools/pre-execute',async()=>({kind:'allow'}));",
 " let busy=false;const dir="+JSON.stringify(home)+";const timer=setInterval(async()=>{",
 "  if(busy||!existsSync(path.join(dir,'request.json')))return;",
 "  busy=true;",
 "  try {",
 "   const {sessionId}=JSON.parse(readFileSync(path.join(dir,'request.json'),'utf8'));const agent=ctx.agents.get(sessionId);",
 "   if(!agent)throw Error('Agent unavailable');",
 "   const run=(name,args={})=>ctx.tools.execute({agent,name,arguments:args,callId:Math.random().toString(36),signal:new AbortController().signal});",
 "   const ordinary=await run('workflow_probe_forbidden');const before=bodies;",
 "   const provenance=await ctx.tools.execute({agent,name:'mcp__biodsh_ssh__server_write',arguments:{path:'/test/new',content:'test',_sessionId:'forged'},callId:'provenance',signal:new AbortController().signal});",
 "   const selection={workflow:'rnaseq',version:'20261014.1',serverId:'selected-server',source:'shared',input:'.',prefix:'omaax',steps:['rnaseq_qc','rnaseq_de'],parameters:{rnaseq_qc:{adapter_r1:'AGATCGGAAGAGC'},rnaseq_de:{padj:0.05,config:'literal-{{sample}}.json'}},configs:{},cpu:2,memoryMb:2048,timeoutSeconds:3600};",
 "   const preStep=()=>agentEvents(ctx,agent).waterfall('agent/pre-step',{turn:1,step:200,messages:[],signal:new AbortController().signal},async()=>({kind:'enter',messages:[]}));",
 "   const ordinaryBudget=await preStep();",
 "   const beforePrompt=renderPrompt(await ctx.systemPrompt.assemble({agent,scope:agent}));",
 "   writeFileSync(path.join(dir,'workflow-bindings',sessionId+'.json'),JSON.stringify({schema:1,sessionId,workflow:'rnaseq',version:'20261014.1',selection}));",
 "   const workflowBudget=await preStep();",
 "   const environmentTools={};for(const name of ['workflow_readiness','workflow_prepare','workflow_prepare_status','workflow_environment','server_delete_confirm'])environmentTools[name]=await run('mcp__biodsh_ssh__'+name);",
 "   const prompt=renderPrompt(await ctx.systemPrompt.assemble({agent,scope:agent}));",
 "   const denied=await run('workflow_probe_forbidden');const allowed=await run('mcp__biodsh_ssh__workflow_catalog');",
 "   let authenticated=true;ctx.tools.guard(createGuard(()=>({ok:authenticated})));",
 "   const authAllowed=await run('mcp__biodsh_ssh__workflow_prepare');const research={};for(const [name,args] of [['read',{file_path:path.join(dir,'workspace','environment-guide.txt')}],['grep',{pattern:'ENV_REPAIR_PROBE',path:path.join(dir,'workspace')}],['glob',{pattern:'*.txt',path:path.join(dir,'workspace')}]])research[name]=await run(name,args);authenticated=false;const deniedRead=await run('read',{file_path:path.join(dir,'workspace','environment-guide.txt')});const authDenied=await run('mcp__biodsh_ssh__workflow_prepare');",
 "   const budgetFile=path.join(dir,'workflow-bindings','budget-'+sessionId+'.json');const savedBudget=JSON.parse(readFileSync(budgetFile,'utf8'));writeFileSync(budgetFile,JSON.stringify({...savedBudget,tokens:200000}));const budgetStopped=await preStep();",
 "   writeFileSync(path.join(dir,'result.json'),JSON.stringify({ordinary,denied,allowed,provenance,sessionId,before,after:bodies,selection,beforePrompt,prompt,ordinaryBudget,workflowBudget,environmentTools,authAllowed,authDenied,budgetStopped,research,deniedRead}));",
 "  } catch(e){writeFileSync(path.join(dir,'result.json'),JSON.stringify({error:String(e)}));}",
 " },100);",
 " ctx.effect(()=>()=>clearInterval(timer));",
 "}"
].join('\n'));
writeFileSync(path.join(home,'cordis.patch.yml'),'- insert:\n    - id: omaax-workflow-guard\n      name: '+JSON.stringify(pathToFileURL(path.join(desktop,'dsh-plugins/workflow-guard/index.js')).href)+'\n    - id: biodsh-step-guard\n      name: '+JSON.stringify(pathToFileURL(path.join(desktop,'dsh-plugins/step-guard/index.js')).href)+'\n    - id: workflow-sdk-probe\n      name: '+JSON.stringify(pathToFileURL(fixture).href)+'\n');
const workspace=path.join(home,'workspace');mkdirSync(workspace);writeFileSync(path.join(workspace,'environment-guide.txt'),'ENV_REPAIR_PROBE: use Conda packages and inspect logs.');
const child=spawn(process.execPath,['--expose-internals',path.join(sdk,'lib/bin.js'),'web','--port','0','--no-open'],{cwd:workspace,windowsHide:true,env:{...process.env,DSH_HOME:home,DSH_TELEMETRY_DISABLED:'1',OMAAX_WORKFLOW_GUARD_DIR:bindings,DEEPSEEK_API_KEY:'',OPENAI_API_KEY:''}});
let log='';
const url=await new Promise((resolve,reject)=>{
 const timeout=setTimeout(()=>reject(Error('SDK startup timeout: '+log.slice(-1800))),40000);
 const scan=b=>{log+=b.toString();const m=log.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/);if(m){clearTimeout(timeout);resolve(m[0]);}};
 child.stdout.on('data',scan);child.stderr.on('data',scan);child.on('exit',code=>{clearTimeout(timeout);reject(Error('SDK exit '+code+': '+log.slice(-1800)));});
}).catch(e=>{child.kill();throw e;});
try {
 const response=await fetch(url,{redirect:'manual'});const cookie=response.headers.getSetCookie().map(s=>s.split(';')[0]).find(s=>s.startsWith('dsh-auth-'));
 const origin=new URL(url).origin;
 const rpc=async(endpoint,wire,body)=>{const r=await fetch(origin+'/api/'+endpoint,{method:'POST',headers:{'Content-Type':'application/json',Cookie:cookie},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),method:endpoint,payload:{args:{[wire]:body}}})});return (await r.json()).result;};
 const ws=await rpc('workspace/create','request',{path:workspace});const wid=ws.value?.workspace?.workspaceId||ws.value?.workspaceId;assert.ok(wid,JSON.stringify(ws));
 const session=await rpc('session/create','request',{workspaceId:wid});const sid=session.value?.sessionId||session.value?.session?.sessionId||session.value?.id;assert.ok(sid,JSON.stringify(session));
 assert.ok(existsSync(path.join(bindings,'ready.json')),'Guard must register successfully in real SDK');
 assert.equal(JSON.parse(readFileSync(path.join(bindings,'ready.json'))).selectionContext,true);
 writeFileSync(path.join(home,'request.json'),JSON.stringify({sessionId:sid}));
 const deadline=Date.now()+15000;
 while(!existsSync(path.join(home,'result.json'))&&Date.now()<deadline)await new Promise(r=>setTimeout(r,100));
 const result=JSON.parse(readFileSync(path.join(home,'result.json'),'utf8'));
 assert.ok(!result.error,JSON.stringify(result));
 assert.equal(result.budgetStopped.kind,"enter","Usage accounting must not stop the SDK at the former token ceiling");
 for(const name of ['read','grep'])assert.ok(JSON.stringify(result.research[name]).includes('ENV_REPAIR_PROBE'),JSON.stringify(result.research[name]));
 assert.ok(JSON.stringify(result.research.glob).includes('environment-guide.txt'));
 assert.ok(JSON.stringify(result.deniedRead).includes('账号未登录'));
 assert.ok(JSON.stringify(result.authAllowed).includes("ENV_ALLOWED_workflow_prepare"));
 assert.ok(!JSON.stringify(result.authDenied).includes("ENV_ALLOWED_"));
 assert.ok(JSON.stringify(result.authDenied).includes("账号未登录"));
 assert.ok(JSON.stringify(result.ordinaryBudget).includes('不要再调用任何工具'));
 assert.ok(!JSON.stringify(result.workflowBudget).includes('不要再调用任何工具'));
 assert.ok(JSON.stringify(result.ordinary).includes('FORBIDDEN_BODY'),'Fixture must be callable before workflow selection');
 assert.ok(JSON.stringify(result.denied).includes('一体化分析流程对话'),'Real SDK must report our guard denial, not an unknown-tool error');
 assert.ok(JSON.stringify(result.allowed).includes('ALLOWED_BODY'));
 assert.ok(JSON.stringify(result.provenance).includes(result.sessionId),'Real SDK must pass agent identity to the execution body');
 assert.ok(!JSON.stringify(result.provenance).includes('forged'),'Model cannot choose another conversation identity');
 for(const name of ['workflow_readiness','workflow_prepare','workflow_prepare_status','workflow_environment'])assert.ok(JSON.stringify(result.environmentTools[name]).includes('ENV_ALLOWED_'+name));
 assert.ok(!JSON.stringify(result.environmentTools.server_delete_confirm).includes('ENV_ALLOWED_'));
 assert.equal(result.before,result.after,'An allow pre-execute listener must not override the monotonic guard');
 assert.ok(!result.beforePrompt.includes('OMaaX 内部流程选择'));
 assert.ok(result.prompt.includes(JSON.stringify(result.selection)),'Real model prompt must keep exact selection including resources and literal braces');
 assert.ok(result.prompt.includes('workflow_catalog')&&result.prompt.includes('workflow_plan'));
 assert.ok(result.prompt.includes('普通用户的新 Conda 环境')&&result.prompt.includes('临时安装和分析应放在同一次任务中')&&result.prompt.includes('read、grep、glob'));
 assert.ok(!result.prompt.includes('脚本只能写 /output'));
 writeFileSync(path.join(base,'latest.json'),JSON.stringify({home,sdkGuardVerified:true,result},null,2));
 console.log(JSON.stringify({sdkGuardVerified:true,bodiesBefore:result.before,bodiesAfter:result.after,home}));
} finally {child.kill();}
