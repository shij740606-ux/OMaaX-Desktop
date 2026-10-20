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
const mcpScript=path.join(home,'mcp-fixture.cjs');
writeFileSync(mcpScript,`const readline=require('node:readline');const fs=require('node:fs');let calls=0;
readline.createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line);if(r.id===undefined)return;let result;
if(r.method==='initialize')result={protocolVersion:r.params.protocolVersion,capabilities:{tools:{}},serverInfo:{name:'isolated-fixture',version:'1'}};
else if(r.method==='tools/list')result={tools:[{name:'write',description:'Isolated test marker',inputSchema:{type:'object',properties:{}}}]};
else if(r.method==='tools/call'){fs.writeFileSync(${JSON.stringify(path.join(home,'mcp-calls.json'))},JSON.stringify(++calls));result={content:[{type:'text',text:'external success'}]};}
else result={};process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:r.id,result})+'\\n');});`);
writeFileSync(fixture,`
import {defineContentToolFixture} from ${JSON.stringify(pathToFileURL(path.join(sdk,'node_modules/@deepseek-ai/dsh-tools/lib/index.js')).href)};
import {createGuard,externalPolicy} from ${JSON.stringify(pathToFileURL(path.join(desktop,'dsh-plugins/auth-guard/index.js')).href)};
import {createImageTool} from ${JSON.stringify(pathToFileURL(path.join(desktop,'dsh-plugins/auth-guard/image.js')).href)};
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
import path from 'node:path';
export const inject=['tools','agents','approval'];
export function apply(ctx){
 let outcome='rejected',logged=true,bodies=0,busy=false,bypass=false;
 const policy=externalPolicy(['probe'],{request:async()=>outcome});
 ctx.on('tools/pre-execute',async(exec,next)=>bypass?{kind:'allow'}:policy.before(exec,next));
 ctx.tools.guard(createGuard(()=>({ok:logged}),policy.allowed));

 const dir=${JSON.stringify(home)};
 ctx.tools.register(createImageTool({OMAAX_IMAGE_WORKSPACE:dir,BIODSH_IMAGE_BASE_URL:'https://example.test',BIODSH_IMAGE_MODEL:'test',BIODSH_IMAGE_API_KEY:'synthetic',BIODSH_FILES_URL:'http://127.0.0.1:123/f/token'},async()=>new Response(JSON.stringify({data:[{b64_json:'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhXkAAAAASUVORK5CYII='}]}))));
 const timer=setInterval(async()=>{
  if(busy||!existsSync(path.join(dir,'request.json'))||!ctx.tools.get('mcp__probe__write'))return;busy=true;
  try{
   const {sessionId}=JSON.parse(readFileSync(path.join(dir,'request.json'),'utf8'));const agent=ctx.agents.get(sessionId);
   const run=(name,args={})=>ctx.tools.execute({agent,name,arguments:args,callId:Math.random().toString(36),signal:new AbortController().signal});
   const rejected=await run('mcp__probe__write');outcome='allowed-once';
   const allowed=await run('mcp__probe__write');logged=false;const expired=await run('mcp__probe__write');logged=true;
   bypass=true;const bypassed=await run('mcp__probe__write');bypass=false;
   const image=await run('omaax_generate_image',{prompt:'cell'});
   writeFileSync(path.join(dir,'result.json'),JSON.stringify({rejected,allowed,expired,bypassed,image,bodies:JSON.parse(readFileSync(path.join(dir,'mcp-calls.json'),'utf8')),approvalAvailable:typeof ctx.approval.request==='function'}));
  }catch(e){writeFileSync(path.join(dir,'result.json'),JSON.stringify({error:String(e)}));}
 },100);ctx.effect(()=>()=>clearInterval(timer));
}
`);
writeFileSync(path.join(home,'cordis.patch.yml'),'- insert:\n    - id: external-sdk-probe\n      name: '+JSON.stringify(pathToFileURL(fixture).href)+'\n    - id: external-mcp-probe\n      name: \"@deepseek-ai/dsh-mcp-client\"\n      config:\n        serverName: probe\n        transport: stdio\n        command: '+JSON.stringify(process.execPath)+'\n        args: ['+JSON.stringify(mcpScript)+']\n');
const workspace=path.join(home,'workspace');mkdirSync(workspace);
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


 writeFileSync(path.join(home,'request.json'),JSON.stringify({sessionId:sid}));
 const deadline=Date.now()+15000;
 while(!existsSync(path.join(home,'result.json'))&&Date.now()<deadline)await new Promise(r=>setTimeout(r,100));
 const result=JSON.parse(readFileSync(path.join(home,'result.json'),'utf8'));
 assert.ok(!result.error,JSON.stringify(result));
 assert.equal(result.approvalAvailable,true);
 assert.equal(result.bodies,1);
 assert.equal(result.rejected.isError,true);
 assert.equal(result.expired.isError,true);
 assert.equal(result.bypassed.isError,true);
 assert.equal(result.allowed.isError,false);
 assert.equal(result.image.isError,false,JSON.stringify(result.image));
 assert.match(JSON.stringify(result.image),/生成的插图/);
 console.log(JSON.stringify({passed:7,home,mode:'real SDK and stdio MCP transport, synthetic server and image API'}));
} finally {child.kill();}
