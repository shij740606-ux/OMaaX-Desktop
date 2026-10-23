// Exercise the real Python client through the company bridge without provider credentials.
import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';
const run=promisify(execFile);
const desktop=fileURLToPath(new URL('../',import.meta.url));
const bridgeFile=process.env.OMAAX_TEST_COMPANY_BRIDGE || path.join(desktop,'resources/scripts/company-llm.mjs');
const {startBridge}=await import(pathToFileURL(bridgeFile));
const python=process.env.OMAAX_TEST_PYTHON || path.join(desktop,'../validation-local/edu-test-env/Scripts/python.exe');
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
const program=`
import asyncio,json,sys
from app.services.llm import complete_text,complete_json,LLMError
async def main():
 try:
  result=await (complete_json('system','question') if sys.argv[1]=='json' else complete_text('system','question'))
  print(json.dumps({'result':result}))
 except LLMError as e: print(json.dumps({'error':str(e)},ensure_ascii=False))
asyncio.run(main())
`;
for(const scenario of ['text','json','expired','forbidden','refresh']){
 test(`education company route: ${scenario}`,async()=>{
  let calls=0,mints=0;
  const bridge=await startBridge({key:'test-loopback-only',session:async()=>scenario==='expired'?{}:{hub:'https://account.invalid',token:'test-login'},fetcher:async(url,init)=>{
   if(url.endsWith('/api/llm/models'))return json({ok:true,models:scenario==='forbidden'?[]:[{id:'authorized-model',enabled:true}]});
   if(url.endsWith('/api/llm/token'))return json({ok:true,token:`test-short-lived-${++mints}`,expires_in:600});
   calls++;
   assert.equal(init.headers.Authorization,`Bearer test-short-lived-${mints}`);
   const body=JSON.parse(init.body);assert.equal(body.model,'authorized-model');
   assert.equal(body.messages[1].content,'question');
   if(scenario==='refresh'&&calls===1)return json({error:{code:'TOKEN_EXPIRED'}},401);
   return json({choices:[{message:{content:scenario==='json'?'{"ok":true}':'education answer'}}]});
  }});
  try{
   const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1',PYTHONPATH:path.join(desktop,'resources/education-service')};
   for(const key of Object.keys(env))if(/^(REVIEW_|DOCGEN_LLM_)|^(DEEPSEEK_API_KEY|OPENAI_API_KEY|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY)$/i.test(key))delete env[key];
   Object.assign(env,{REVIEW_LLM_BASE_URL:`http://127.0.0.1:${bridge.port}/v1`,REVIEW_LLM_API_KEY:'test-loopback-only',REVIEW_LLM_MODEL:'omaax'});
   const {stdout}=await run(python,['-c',program,scenario],{env,timeout:30000});
   const result=JSON.parse(stdout);
   if(scenario==='expired'){assert.match(result.error,/登录已失效/);assert.equal(calls,0);}
   else if(scenario==='forbidden'){assert.match(result.error,/账号暂不能/);assert.equal(calls,0);}
   else {assert.deepEqual(result.result,scenario==='json'?{ok:true}:'education answer');assert.equal(calls,scenario==='refresh'?2:1);}
  }finally{await bridge.close();}
 });
}
