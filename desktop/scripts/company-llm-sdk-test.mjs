import {spawn} from 'node:child_process';
import {mkdirSync,mkdtempSync,writeFileSync,readFileSync,existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {startBridge} from '../resources/scripts/company-llm.mjs';
import {companyErrors} from './patch-dsh-company-errors.mjs';
const desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const base=path.resolve(desktop,'../validation-local/company-sdk');mkdirSync(base,{recursive:true});
const home=mkdtempSync(path.join(base,'home-')),sdk=path.join(process.env.OMAAX_TEST_DSH_RUNTIME?path.resolve(process.env.OMAAX_TEST_DSH_RUNTIME):path.join(desktop,'dsh-runtime'),'node_modules/@deepseek-ai/dsh');
const sdkRequire=createRequire(path.join(sdk,'package.json'));
let requests=0;const efforts=[];
const bridge=await startBridge({key:'synthetic',session:()=>({hub:'https://test.invalid',token:'user'}),fetcher:async(url,init)=>{
 if(url.endsWith('/models'))return Response.json({ok:true,models:[{id:'authorized',contextLength:1048576,modelEffort:{levels:['low','high','max'],default:'high'}}]});
 if(url.endsWith('/token'))return Response.json({ok:true,token:'proxy',expires_in:600});
 const body=JSON.parse(init.body);assert.equal(body.model,'authorized');assert.ok(!('max_tokens' in body));efforts.push(body.effort);requests++;
 return new Response('data: {"id":"test","choices":[{"index":0,"delta":{"role":"assistant","content":"SDK success","tool_calls":[{"index":0,"id":"call1","type":"function","function":{"name":"probe","arguments":"{}"}}]},"finish_reason":null}]}\n\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}}\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
}});
const capabilities=await fetch(`http://127.0.0.1:${bridge.port}/v1/modes`,{headers:{Authorization:'Bearer synthetic'}}).then(r=>r.json());
assert.equal(capabilities.contextWindow,1048576);
// Feed the bridge's public error contract through the real provider SDK. Normal
// calls still exercise the complete company bridge; no production endpoint is used.
const errorRequests=[];
const fixture=createServer(async(req,res)=>{
 let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);
 const code=JSON.stringify(body.messages).match(/ERROR_CASE:([a-z_]+)/)?.[1];
 if(code){assert.ok(Object.hasOwn(companyErrors,code));errorRequests.push(code);res.writeHead(503,{'content-type':'application/json'});res.end(JSON.stringify({error:{code,message:'Synthetic private provider diagnostic'}}));return;}
 const upstream=await fetch(`http://127.0.0.1:${bridge.port}${req.url}`,{method:'POST',headers:{Authorization:'Bearer synthetic','content-type':'application/json'},body:raw});
 res.writeHead(upstream.status,Object.fromEntries(upstream.headers));for await(const chunk of upstream.body)res.write(chunk);res.end();
});
await new Promise(resolve=>fixture.listen(0,'127.0.0.1',resolve));
const fixturePort=fixture.address().port;
const plugin=path.join(home,'probe.mjs');
writeFileSync(plugin,`
import {createUserMessage} from ${JSON.stringify(pathToFileURL(sdkRequire.resolve('@deepseek-ai/dsh-llm')).href)};
import {writeFileSync} from 'node:fs';
export const inject=['llm'];
export function apply(ctx){let busy=false;const t=setInterval(async()=>{if(busy||!ctx.llm.listProviders().length)return;busy=true;
 try{const info=await ctx.llm.resolveModelInfo('deepseek-official','omaax-high');const chunks=[];for(const mode of ['low','high','max','legacy'])for await(const c of ctx.llm.stream({provider:'deepseek-official',model:mode==='legacy'?'deepseek-flash':'omaax-'+mode,reasoningEffort:'high',messages:[createUserMessage({content:[{type:'text',text:'test'}]})]}))chunks.push(c);
 const errors={};for(const code of ${JSON.stringify(Object.keys(companyErrors))}){errors[code]=[];for await(const c of ctx.llm.stream({provider:'deepseek-official',model:'omaax-high',messages:[createUserMessage({content:[{type:'text',text:'ERROR_CASE:'+code}]})]}))errors[code].push(c);}
 writeFileSync(${JSON.stringify(path.join(home,'result.json'))},JSON.stringify({chunks,errors,context:info.context}));}
 catch(e){writeFileSync(${JSON.stringify(path.join(home,'result.json'))},JSON.stringify({error:String(e)}));}clearInterval(t);},100);ctx.effect(()=>()=>clearInterval(t));}
`);
writeFileSync(path.join(home,'settings.yaml'),`llm-deepseek:\n  baseURL: http://127.0.0.1:${bridge.port}/v1\n  apiKeyEnv: OMAAX_TEST_KEY\n  models:\n    - id: omaax\n      name: OMaaX\n`);
writeFileSync(path.join(home,'cordis.patch.yml'),`- id: llm-deepseek\n  config:\n    baseURL: http://127.0.0.1:${bridge.port}/v1\n    apiKeyEnv: OMAAX_TEST_KEY\n    defaultContextWindow: ${capabilities.contextWindow}\n    models:\n${['low','high','max'].map(mode=>`      - id: omaax-${mode}\n        name: OMaaX\n`).join('')}- insert:\n    - id: company-probe\n      name: ${JSON.stringify(pathToFileURL(plugin).href)}\n`);
if(JSON.parse(readFileSync(path.join(sdk,'package.json'),'utf8')).version.startsWith('0.2.')) {
 writeFileSync(path.join(home,'cordis.patch.yml'),`- id: llm-deepseek\n  disabled: true\n- id: llm-pi-ai\n  config:\n    providers:\n      deepseek-official:\n        api: openai-completions\n        baseURL: http://127.0.0.1:${bridge.port}/v1\n        apiKeyEnv: OMAAX_TEST_KEY\n        defaultContextWindow: ${capabilities.contextWindow}\n        models:\n${['omaax-low','omaax-high','omaax-max','deepseek-flash'].map(mode=>`          - id: ${mode}\n            name: OMaaX\n            reasoningEfforts: {off: null, minimal: minimal, low: low, medium: medium, high: high, xhigh: xhigh, max: max}\n`).join('')}- insert:\n    - id: company-probe\n      name: ${JSON.stringify(pathToFileURL(plugin).href)}\n`);
}

for(const name of ['cordis.patch.yml','settings.yaml']){const file=path.join(home,name);writeFileSync(file,readFileSync(file,'utf8').replaceAll(`127.0.0.1:${bridge.port}`,`127.0.0.1:${fixturePort}`));}
const child=spawn(process.execPath,['--expose-internals',path.join(sdk,'lib/bin.js'),'web','--port','0','--no-open'],{cwd:home,windowsHide:true,env:{...process.env,DSH_HOME:home,OMAAX_TEST_KEY:'synthetic',DSH_TELEMETRY_DISABLED:'1',DEEPSEEK_API_KEY:'',OPENAI_API_KEY:''}});
let log='';child.stdout.on('data',b=>log+=b);child.stderr.on('data',b=>log+=b);
try{
 const deadline=Date.now()+45000;while(!existsSync(path.join(home,'result.json'))&&Date.now()<deadline)await new Promise(r=>setTimeout(r,100));
 writeFileSync(path.join(home,'sdk.log'),log);assert.ok(existsSync(path.join(home,'result.json')),'SDK probe timeout');
 const result=JSON.parse(readFileSync(path.join(home,'result.json'),'utf8'));assert.ok(!result.error,JSON.stringify(result));
 assert.equal(result.context.contextWindow,1048576);
 assert.match(JSON.stringify(result.chunks),/SDK success/);assert.match(JSON.stringify(result.chunks),/call1/);assert.match(JSON.stringify(result.chunks),/usage/);assert.equal(requests,4);assert.deepEqual(efforts,['low','high','max','high']);
 for(const code of Object.keys(companyErrors))assert.equal(result.errors[code].find(c=>c.type==='finish')?.reason?.failure?.code,code,JSON.stringify(result.errors[code]));
 assert.deepEqual(errorRequests,Object.keys(companyErrors),'provider SDK must not silently retry or reclassify company errors');
 console.log(JSON.stringify({passed:7+errorRequests.length,home,checks:['SDK company config','1M context','service-managed output limit','response content','tool call','usage','legacy model and reasoning selection',`${errorRequests.length} company error codes preserved through actual provider SDK`]}));
}finally{child.kill();fixture.closeAllConnections();await new Promise(resolve=>fixture.close(resolve));await bridge.close();}
