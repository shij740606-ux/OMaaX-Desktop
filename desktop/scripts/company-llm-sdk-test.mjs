import {spawn} from 'node:child_process';
import {mkdirSync,mkdtempSync,writeFileSync,readFileSync,existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
import {startBridge} from '../resources/scripts/company-llm.mjs';
const desktop=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const base=path.resolve(desktop,'../validation-local/company-sdk');mkdirSync(base,{recursive:true});
const home=mkdtempSync(path.join(base,'home-')),sdk=path.join(desktop,'dsh-runtime/node_modules/@deepseek-ai/dsh');
let requests=0;const efforts=[];
const bridge=await startBridge({key:'synthetic',session:()=>({hub:'https://test.invalid',token:'user'}),fetcher:async(url,init)=>{
 if(url.endsWith('/models'))return Response.json({ok:true,models:[{id:'authorized',modelEffort:{levels:['low','high','max'],default:'high'}}]});
 if(url.endsWith('/token'))return Response.json({ok:true,token:'proxy',expires_in:600});
 const body=JSON.parse(init.body);assert.equal(body.model,'authorized');efforts.push(body.effort);requests++;
 return new Response('data: {"id":"test","choices":[{"index":0,"delta":{"role":"assistant","content":"SDK success","tool_calls":[{"index":0,"id":"call1","type":"function","function":{"name":"probe","arguments":"{}"}}]},"finish_reason":null}]}\n\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}}\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
}});
const plugin=path.join(home,'probe.mjs');
writeFileSync(plugin,`
import {createUserMessage} from ${JSON.stringify(pathToFileURL(path.join(sdk,'node_modules/@deepseek-ai/dsh-llm/lib/index.js')).href)};
import {writeFileSync} from 'node:fs';
export const inject=['llm'];
export function apply(ctx){let busy=false;const t=setInterval(async()=>{if(busy||!ctx.llm.listProviders().length)return;busy=true;
 try{const chunks=[];for(const mode of ['low','high','max'])for await(const c of ctx.llm.stream({provider:'deepseek-official',model:'omaax-'+mode,messages:[createUserMessage({content:[{type:'text',text:'test'}]})]}))chunks.push(c);writeFileSync(${JSON.stringify(path.join(home,'result.json'))},JSON.stringify({chunks}));}
 catch(e){writeFileSync(${JSON.stringify(path.join(home,'result.json'))},JSON.stringify({error:String(e)}));}clearInterval(t);},100);ctx.effect(()=>()=>clearInterval(t));}
`);
writeFileSync(path.join(home,'settings.yaml'),`llm-deepseek:\n  baseURL: http://127.0.0.1:${bridge.port}/v1\n  apiKeyEnv: OMAAX_TEST_KEY\n  models:\n    - id: omaax\n      name: OMaaX\n`);
writeFileSync(path.join(home,'cordis.patch.yml'),`- id: llm-deepseek\n  config:\n    baseURL: http://127.0.0.1:${bridge.port}/v1\n    apiKeyEnv: OMAAX_TEST_KEY\n    maxTokens: 8192\n    defaultContextWindow: 65536\n    models:\n${['low','high','max'].map(mode=>`      - id: omaax-${mode}\n        name: OMaaX\n`).join('')}- insert:\n    - id: company-probe\n      name: ${JSON.stringify(pathToFileURL(plugin).href)}\n`);
const child=spawn(process.execPath,['--expose-internals',path.join(sdk,'lib/bin.js'),'web','--port','0','--no-open'],{cwd:home,windowsHide:true,env:{...process.env,DSH_HOME:home,OMAAX_TEST_KEY:'synthetic',DSH_TELEMETRY_DISABLED:'1',DEEPSEEK_API_KEY:'',OPENAI_API_KEY:''}});
let log='';child.stdout.on('data',b=>log+=b);child.stderr.on('data',b=>log+=b);
try{
 const deadline=Date.now()+45000;while(!existsSync(path.join(home,'result.json'))&&Date.now()<deadline)await new Promise(r=>setTimeout(r,100));
 writeFileSync(path.join(home,'sdk.log'),log);assert.ok(existsSync(path.join(home,'result.json')),'SDK probe timeout');
 const result=JSON.parse(readFileSync(path.join(home,'result.json'),'utf8'));assert.ok(!result.error,JSON.stringify(result));
 assert.match(JSON.stringify(result.chunks),/SDK success/);assert.match(JSON.stringify(result.chunks),/call1/);assert.match(JSON.stringify(result.chunks),/usage/);assert.equal(requests,3);assert.deepEqual(efforts,['low','high','max']);
 console.log(JSON.stringify({passed:4,home,checks:['SDK company config','response content','tool call','usage']}));
}finally{child.kill();await bridge.close();}
