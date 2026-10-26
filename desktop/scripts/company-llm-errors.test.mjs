import {test} from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {startBridge,createNativeSessionReader} from '../resources/scripts/company-llm.mjs';

const secret='PRIVATE_TOKEN_OR_BODY';
const auth={hub:'https://fake-auth.invalid',token:secret};
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
function reader(runner){return createNativeSessionReader({executable:'fake.exe',root:'fake-root',accountHome:'fake-base',runner});}
function helperFailure(code,status){return reader(async()=>{throw Object.assign(Error(secret),{code:1,stdout:JSON.stringify({ok:false,error:{code,status,message:secret,retryable:false}}),stderr:secret});});}
async function fixture(fn,{session=()=>auth,fetcher}={}){
 let forwards=0;
 const bridge=await startBridge({key:'local-test',session,fetcher:async(url,init)=>{
  const stage=url.endsWith('/api/llm/models')?'models':url.endsWith('/api/llm/token')?'token':'upstream';
  if(stage==='upstream')forwards++;
  const mocked=await fetcher?.(stage,init);if(mocked)return mocked;
  if(stage==='models')return json({ok:true,models:[{id:'fake-model'}]});
  if(stage==='token')return json({ok:true,token:'fake-proxy-token',expires_in:600});
  return json({choices:[{message:{content:'ok'}}]});
 }});
 const request=(body={})=>fetch(`http://127.0.0.1:${bridge.port}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer local-test'},body:JSON.stringify({messages:[{role:'user',content:'hello'}],...body})});
 const modes=()=>fetch(`http://127.0.0.1:${bridge.port}/v1/modes`,{headers:{Authorization:'Bearer local-test'}});
 try{await fn({request,modes,forwards:()=>forwards});}finally{await bridge.close();}
}
async function check(response,code,status,retryable){
 assert.equal(response.status,status);const text=await response.text();assert.doesNotMatch(text,/PRIVATE_TOKEN_OR_BODY|fake-auth|fake-proxy-token|stderr/);
 const error=JSON.parse(text).error;assert.equal(error.code,code);if(retryable!==undefined)assert.equal(error.retryable,retryable);return error;
}

test('native helper success keeps the established session and child-process isolation',async()=>{
 const session=await reader(async(executable,args,options)=>{
  assert.equal(executable,'fake.exe');assert.deepEqual(args,['--company-llm-session']);
  assert.equal(options.windowsHide,true);assert.equal(options.timeout,30000);assert.equal(options.maxBuffer,16384);
  assert.equal(options.env.BIODSH_HOME,'fake-root');assert.equal(options.env.OMAAX_ACCOUNT_HOME,'fake-base');
  return {stdout:JSON.stringify(auth)};
 })();assert.deepEqual(session,auth);
});

test('real child-process exit and timeout retain classification through the HTTP bridge',async t=>{
 const execute=promisify(execFile);
 for(const [name,source,code,status,deadline] of [
  ['typed network failure',`process.stdout.write(JSON.stringify({ok:false,error:{code:'auth_network_error',status:503,message:'${secret}'}}));process.exitCode=1;`,'auth_network_error',503,5000],
  ['invalid output',`process.stdout.write('${secret}');process.exitCode=1;`,'auth_helper_invalid_response',502,5000],
  ['hung helper','setInterval(()=>{},1000);','auth_helper_timeout',504,150],
 ])await t.test(name,()=>fixture(async({request,forwards})=>{
  await check(await request(),code,status);assert.equal(forwards(),0);
 },{session:reader((_executable,_args,options)=>execute(process.execPath,['-e',source],{...options,timeout:deadline}))}));
});

test('native typed failures survive both bridge endpoints without leaking messages',async t=>{
 for(const [code,status,retryable] of [
  ['auth_session_expired',401,false],['auth_forbidden',403,false],['auth_network_error',503,true],
  ['auth_timeout',504,true],['auth_service_unavailable',502,true],['auth_service_unavailable',503,true],
  ['auth_invalid_response',502,true],['auth_local_error',502,false],
 ])await t.test(code+' '+status,()=>fixture(async({request,modes,forwards})=>{
  await check(await request(),code,status,retryable);await check(await modes(),code,status,retryable);assert.equal(forwards(),0);
 },{session:helperFailure(code,status)}));
});

test('helper process failures cannot become an invalid API key error',async t=>{
 for(const [name,error,code,status] of [
  ['timeout',{killed:true,code:null,stdout:secret},'auth_helper_timeout',504],
  ['spawn EACCES',{code:'EACCES',stdout:secret},'auth_helper_start_failed',502],
  ['spawn ENOENT',{code:'ENOENT'},'auth_helper_start_failed',502],
  ['oversized output',{killed:true,code:'ERR_CHILD_PROCESS_STDIO_MAXBUFFER'},'auth_helper_invalid_response',502],
  ['bad stdout',{code:1,stdout:secret},'auth_helper_invalid_response',502],
  ['unknown error code',{code:1,stdout:JSON.stringify({ok:false,error:{code:secret,status:401,message:secret}})},'auth_helper_invalid_response',502],
  ['failed process cannot authenticate',{code:1,stdout:JSON.stringify(auth)},'auth_helper_invalid_response',502],
 ])await t.test(name,()=>fixture(async({request,forwards})=>{
  await check(await request(),code,status);assert.equal(forwards(),0);
 },{session:reader(async()=>{throw Object.assign(Error(secret),error);})}));
 for(const stdout of ['invalid',JSON.stringify({hub:auth.hub}),JSON.stringify({ok:false,error:{code:secret}})]){
  await assert.rejects(reader(async()=>({stdout})),error=>error.code==='auth_helper_invalid_response'&&error.status===502);
 }
});

test('models and token endpoints distinguish account, access, throttling and service failures',async t=>{
 for(const stage of ['models','token'])for(const [status,code,out] of [
  [401,'auth_session_expired',401],[403,'model_access_denied',403],[429,'credential_rate_limited',429],
  [400,'credential_invalid_response',502],[404,'credential_invalid_response',502],
  [500,'credential_service_unavailable',500],[503,'credential_service_unavailable',503],[504,'credential_timeout',504],
 ])await t.test(`${stage} ${status}`,()=>fixture(async({request,forwards})=>{
  await check(await request(),code,out);assert.equal(forwards(),0);
 },{fetcher:s=>s===stage?new Response(secret,{status}):undefined}));
});

test('authorization transport failures and malformed responses have distinct safe codes',async t=>{
 for(const stage of ['models','token'])for(const [name,respond,code,status] of [
  ['network',()=>{throw Error(secret);},'credential_network_error',503],
  ['timeout',()=>{throw new DOMException(secret,'TimeoutError');},'credential_timeout',504],
  ['fetch connect timeout',()=>{throw new TypeError(secret,{cause:{code:'UND_ERR_CONNECT_TIMEOUT'}});},'credential_timeout',504],
  ['invalid JSON',()=>new Response(secret),'credential_invalid_response',502],
  ['null JSON',()=>json(null),'credential_invalid_response',502],
  ['API rejected response',()=>json({ok:false,message:secret}),'credential_invalid_response',502],
 ])await t.test(`${stage} ${name}`,()=>fixture(async({request})=>check(await request(),code,status),{fetcher:s=>s===stage?respond():undefined}));
 await fixture(async({request})=>check(await request(),'credential_invalid_response',502),{fetcher:s=>s==='token'?json({ok:true,token:secret,expires_in:0}):undefined});
 await fixture(async({request})=>check(await request(),'model_access_denied',403),{fetcher:s=>s==='models'?json({ok:true,models:[]}):undefined});
});

test('gateway errors are not confused with the login session and preserve the one-refresh limit',async t=>{
 for(const [status,code,calls] of [[401,'model_credential_rejected',2],[403,'model_access_denied',1],[429,'model_rate_limited',1],[400,'model_request_invalid',1],[500,'model_service_unavailable',1],[503,'model_service_unavailable',1],[504,'model_timeout',1]]){
  await t.test(String(status),()=>fixture(async({request,forwards})=>{
   await check(await request(),code,status);assert.equal(forwards(),calls);
  },{fetcher:s=>s==='upstream'?new Response(secret,{status}):undefined}));
 }
 for(const [name,respond,code,status] of [
  ['network',()=>{throw Error(secret);},'model_network_error',502],
  ['timeout',()=>{throw new DOMException(secret,'TimeoutError');},'model_timeout',504],
  ['fetch connect timeout',()=>{throw new TypeError(secret,{cause:{code:'UND_ERR_CONNECT_TIMEOUT'}});},'model_timeout',504],
  ['wrong content type',()=>new Response(secret),'model_invalid_response',502],
 ])await t.test(name,()=>fixture(async({request,forwards})=>{
  await check(await request(),code,status);assert.equal(forwards(),1);
 },{fetcher:s=>s==='upstream'?respond():undefined}));
});

test('watch failures before headers preserve the auth network reason without replay',async t=>{
 const interval=globalThis.setInterval;t.mock.method(globalThis,'setInterval',(callback,ms,...args)=>interval(callback,ms===5000?30:ms,...args));
 let reads=0;const fail=helperFailure('auth_network_error',503);
 await fixture(async({request,forwards})=>{await check(await request({stream:true}),'auth_network_error',503,true);assert.equal(forwards(),1);},{
  session:()=>++reads>2?fail():auth,
  fetcher:(stage,init)=>stage==='upstream'?new Promise((_resolve,reject)=>init.signal.addEventListener('abort',()=>reject(Error(secret)),{once:true})):undefined,
 });
});

test('watch failure after streaming starts only interrupts the stream and never replays',async t=>{
 const interval=globalThis.setInterval;t.mock.method(globalThis,'setInterval',(callback,ms,...args)=>interval(callback,ms===5000?40:ms,...args));
 let reads=0;const fail=helperFailure('auth_timeout',504);
 await fixture(async({request,forwards})=>{
  const response=await request({stream:true});assert.equal(response.status,200);const reader=response.body.getReader();
  assert.match(new TextDecoder().decode((await reader.read()).value),/started/);
  await assert.rejects(async()=>{while(!(await reader.read()).done){}});assert.equal(forwards(),1);
 },{session:()=>++reads>2?fail():auth,fetcher:(stage,init)=>stage==='upstream'?new Response(new ReadableStream({start(controller){
  controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"started"}}]}\n\n'));
  init.signal.addEventListener('abort',()=>controller.close(),{once:true});
 }}),{headers:{'content-type':'text/event-stream'}}):undefined});
});

test('a delayed old 401 waits for the refresh of a newer rejected token',async()=>{
 const gate=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
 const oldStarted=gate(),releaseOld=gate(),thirdStarted=gate(),releaseThird=gate();let mints=0,oldSettled=false;const calls=[];
 try{await fixture(async({request,forwards})=>{
  const send=label=>request({messages:[{role:'user',content:label}]}).then(async response=>({status:response.status,body:await response.json()}));
  assert.equal((await send('prime')).status,200);
  const old=send('old').finally(()=>oldSettled=true);await oldStarted.promise;
  assert.equal((await send('second')).status,200);
  const third=send('third');await thirdStarted.promise;releaseOld.resolve();
  await new Promise(r=>setTimeout(r,30));assert.equal(oldSettled,false);
  assert.deepEqual(calls.filter(v=>v.label==='old').map(v=>v.token),['Bearer FAKE_MODEL_1']);
  releaseThird.resolve();assert.equal((await old).status,200);assert.equal((await third).status,200);
  assert.deepEqual(calls.filter(v=>v.label==='old').map(v=>v.token),['Bearer FAKE_MODEL_1','Bearer FAKE_MODEL_3']);
  assert.equal(mints,3);assert.equal(forwards(),7);
 },{fetcher:async(stage,init)=>{
  if(stage==='token'){const count=++mints;if(count===3){thirdStarted.resolve();await releaseThird.promise;}return json({ok:true,token:`FAKE_MODEL_${count}`,expires_in:600});}
  if(stage!=='upstream')return;
  const label=JSON.parse(init.body).messages[0].content,token=init.headers.Authorization;calls.push({label,token});
  if(label==='old'&&token==='Bearer FAKE_MODEL_1'){oldStarted.resolve();await releaseOld.promise;return json({},401);}
  if((label==='second'&&token==='Bearer FAKE_MODEL_1')||(label==='third'&&token==='Bearer FAKE_MODEL_2'))return json({},401);
  return json({choices:[{message:{content:'ok'}}]});
 }});}finally{releaseOld.resolve();releaseThird.resolve();}
});
