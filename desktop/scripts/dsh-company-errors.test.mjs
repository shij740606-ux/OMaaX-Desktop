import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {companyErrors,internalErrorCode,patchCompanyAdapterSource,patchCompanyRetrySource,patchCompanyChatSource,patchCompanyTrajectorySource} from './patch-dsh-company-errors.mjs';

const runtime = new URL('../dsh-runtime/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/',import.meta.url);
const read = name => readFileSync(new URL(name,runtime),'utf8');
const adapter = patchCompanyAdapterSource(read('dsh-llm-deepseek/lib/index.js'));
const retry = patchCompanyRetrySource(read('dsh-llm-retry/lib/index.js'));
const chat = patchCompanyChatSource(read('dsh-client-ui-chat/lib/client.js'));
const trajectory = patchCompanyTrajectorySource(read('dsh-client-ui-trajectory/lib/client.js'));
const httpCode = vm.runInNewContext(adapter.slice(adapter.indexOf('function httpErrorCode('),adapter.indexOf('\n/**',adapter.indexOf('function httpErrorCode(')))+'\nhttpErrorCode',{
  isQuotaExceededError:s=>s==='insufficient_quota', QUOTA_EXCEEDED_CODE:'QUOTA_EXCEEDED',
  isContextWindowExceededError:s=>s==='context_length_exceeded', CONTEXT_WINDOW_EXCEEDED_CODE:'CONTEXT_WINDOW_EXCEEDED',
});
const failureMessage = vm.runInNewContext(chat.slice(chat.indexOf('\t\tfunction failureMessage('),chat.indexOf('\t\tfunction ModelRetryItem('))+'\nfailureMessage');
const retryExpression = retry.match(/else if \((\/\*OMAAX_COMPANY_ERROR_RETRY_V1\*\/[^\n]+)\) return next\(\);/)[1];

test('company classes survive HTTP normalization; provider and context errors stay compatible',()=>{
  for (const code of Object.keys(companyErrors)) assert.equal(httpCode(503,{code}),code);
  assert.equal(httpCode(401,{code:'untrusted_secret'}),'AUTH');
  assert.equal(httpCode(403,{}),'AUTH');
  assert.equal(httpCode(429,{}),'RATE_LIMIT');
  assert.equal(httpCode(503,{}),'SERVER');
  assert.equal(httpCode(400,{code:'context_length_exceeded'}),'CONTEXT_WINDOW_EXCEEDED');
  assert.equal(httpCode(429,{code:'insufficient_quota'}),'QUOTA_EXCEEDED');
});

test('each class shows localized safe wording instead of raw provider messages',()=>{
  for (const [locale,index] of [['zh',0],['en',1]]) {
    const start = chat.indexOf(`const ${locale} = {`);
    const end = chat.indexOf('\n\t\t};',start)+6;
    const dictionary = vm.runInNewContext(chat.slice(start,end)+`; ${locale}`);
    for (const [code,labels] of Object.entries(companyErrors)) {
      const result = failureMessage('secret upstream payload',code,k=>dictionary[k]);
      assert.equal(result,labels[index]);
      assert.ok(!result.includes('secret upstream'));
      if (locale==='en') assert.doesNotMatch(result,/[\p{Script=Han}]/u);
    }
    const legacy = failureMessage('old generic auth', 'AUTH', k=>dictionary[k]);
    assert.ok(legacy.length>0);
    assert.doesNotMatch(legacy,/API 密钥无效|API key is invalid/);
  }
});

test('retry classes retain configured limits and do not retry genuine authorization failures',()=>{
  const defaults = ['SERVER','TIMEOUT','RATE_LIMIT','TRANSPORT'];
  for (const [code,definition] of Object.entries(companyErrors)) {
    const declines = vm.runInNewContext(retryExpression,{policy:{retryableCodes:defaults},failure:{code}});
    assert.equal(declines,!definition[2],code);
  }
  assert.equal(vm.runInNewContext(retryExpression,{policy:{retryableCodes:['RATE_LIMIT']},failure:{code:'auth_network_error'}}),true);
  assert.equal(vm.runInNewContext(retryExpression,{policy:{retryableCodes:['auth_network_error']},failure:{code:'auth_network_error'}}),false);
  assert.match(retry,/previousRetry >= policy.maxRetries/);
});

test('expanded execution details use the same localized categories, including legacy AUTH',()=>{
  const show=vm.runInNewContext(trajectory.slice(trajectory.indexOf('\t\tfunction requestErrorMessage('),trajectory.indexOf('\t\tfunction TokenRows('))+'\nrequestErrorMessage');
  for (const [locale,index] of [['zh',0],['en',1]]) {
    const start=trajectory.indexOf(`const ${locale} = {`),end=trajectory.indexOf('\n\t\t};',start)+6;
    const dictionary=vm.runInNewContext(trajectory.slice(start,end)+`; ${locale}`);
    for (const [code,labels] of Object.entries(companyErrors)) assert.equal(show({errorCode:code,error:'do not display raw secret'},k=>dictionary[k]),labels[index]);
    assert.doesNotMatch(show({errorCode:'AUTH',error:'old secret'},k=>dictionary[k]),/API 密钥无效|API key is invalid|secret/);
  }
});

test('patches are repeatable, reject drift, and participate in staging and release verification',()=>{
  for (const [patch,source] of [[patchCompanyAdapterSource,adapter],[patchCompanyRetrySource,retry],[patchCompanyChatSource,chat],[patchCompanyTrajectorySource,trajectory]]) {
    assert.equal(patch(source),source);
    assert.throws(()=>patch('unexpected upstream layout'),/anchor changed/);
  }
  assert.match(readFileSync(new URL('./stage-dsh.mjs',import.meta.url),'utf8'),/patchCompanyErrors\(dir\)/);
  assert.match(readFileSync(new URL('./verify-stable-runtime.mjs',import.meta.url),'utf8'),/OMAAX_COMPANY_ERROR_UI_V1/);
});

test('sign-in screen strips only known diagnostic prefixes and preserves safe text',()=>{
  const source=readFileSync(new URL('../src/renderer/src/AuthGate.tsx',import.meta.url),'utf8');
  const line=source.split('\n').find(line=>line.startsWith('function loginError(')).replace('value:unknown','value');
  const clean=vm.runInNewContext(line+';loginError');
  assert.equal(clean('Error: AUTH_TIMEOUT: 登录验证超时，请稍后重试。'),'登录验证超时，请稍后重试。');
  assert.equal(clean('UNAUTHORIZED: 登录已过期，请重新登录'),'登录已过期，请重新登录');
  assert.equal(clean('unsafe raw stack\nsecret'),'操作未完成，请重试或联系管理员');
});

test('temporary verification failures retain only the already-open account UI; real revocation closes it',()=>{
  const source=readFileSync(new URL('../src/renderer/src/AuthGate.tsx',import.meta.url),'utf8');
  const line=source.split('\n').find(line=>line.startsWith('export function reconcileHubStatus('))
    .replace('export ','').replaceAll(':HubStatus','');
  const reconcile=vm.runInNewContext(line+';reconcileHubStatus');
  const signedIn={authenticated:true,hub:'https://company.example',user:{id:'one',username:'one'}};
  const unavailable={authenticated:false,hub:signedIn.hub,state:'unavailable',errorCode:'auth_timeout'};
  const retained=reconcile(signedIn,unavailable);
  assert.equal(retained.authenticated,true);
  assert.equal(retained.user,signedIn.user);
  assert.equal(retained.state,'unavailable');
  assert.equal(reconcile({authenticated:false,hub:signedIn.hub},unavailable).authenticated,false);
  assert.equal(reconcile(signedIn,{...unavailable,hub:'https://different.example'}).authenticated,false);
  assert.equal(reconcile(signedIn,{...unavailable,state:'reauth_required',errorCode:'auth_session_expired'}).authenticated,false);
  assert.equal(reconcile(retained,{...signedIn,state:'authenticated'}).errorCode,undefined);
  const native=readFileSync(new URL('../src-tauri/src/lib.rs',import.meta.url),'utf8');
  const handler=native.slice(native.indexOf('async fn hub_status('),native.indexOf('async fn cloud_qa('));
  assert.doesNotMatch(handler,/if value\["authenticated"\] != true/);
});

test('unknown internal errors are localized only inside error renderers',()=>{
 const show=vm.runInNewContext(trajectory.slice(trajectory.indexOf('\t\tfunction requestErrorMessage('),trajectory.indexOf('\t\tfunction TokenRows('))+'\nrequestErrorMessage');
 const cases=[['DeepSeek fetch failed','TRANSPORT','model_network_error'],['Harness timed out','','model_timeout'],['@deepseek-ai/dsh invalid API key','','internal_auth_error'],['DeepSeek rate limit 429','','model_rate_limited'],['failed to import loader entry @deepseek-ai/dsh-client-hmr','','component_load_failed'],['HARNESS unexpected state','','internal_operation_failed']];
 for(const[message,code,expected]of cases){assert.equal(internalErrorCode(message,code),expected);}
 for(const[source,render,prefix]of [[chat,(m,c,t)=>failureMessage(m,c,t),'message'],[trajectory,(m,c,t)=>show({error:m,errorCode:c},t),'details']]){
  const start=source.indexOf('const zh = {'),end=source.indexOf('\n\t\t};',start)+6,dictionary=vm.runInNewContext(source.slice(start,end)+';zh');
  for(const[message,code,expected]of cases){const result=render(message,code,k=>dictionary[k]);assert.equal(result,dictionary[`${prefix}.failure.company.${expected}`]);assert.match(result,/[\p{Script=Han}]/u);assert.doesNotMatch(result,/deepseek|harness|dsh-|plugins\//i);}
  assert.equal(render('磁盘空间不足，请选择其他目录。','',k=>dictionary[k]),'磁盘空间不足，请选择其他目录。');
 }
});
