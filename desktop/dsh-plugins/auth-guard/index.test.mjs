import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createGuard} from './index.js';
test('expired, absent and failing authorization cannot execute',async()=>{
 for(const check of [()=>({ok:false}),()=>null,()=>{throw Error('offline')}])assert.match(await createGuard(check)({name:'mcp__biodsh_ssh__workflow_prepare'}),/账号/);
});
test('autonomous environment repair remains allowed, deletion approvals and bypasses do not',async()=>{
 const guard=createGuard(()=>({ok:true}));
 for(const n of ['server_environment','workflow_readiness','workflow_prepare','workflow_prepare_status','workflow_environment','server_delete','workflow_result'])assert.equal(await guard({name:'mcp__biodsh_ssh__'+n}),undefined);
 for(const name of ['mcp__biodsh_ssh__protected_path_remove','mcp__biodsh_ssh__server_delete_confirm','mcp__biodsh_ssh__server_write_confirm'])assert.match(await guard({name}),/保护/);
});
test('typed login failures remain blocked with specific safe explanations',()=>{
 for(const code of ['auth_session_expired','auth_forbidden','auth_network_error','auth_timeout','auth_service_unavailable','auth_invalid_response','auth_local_error']){
  for(const check of [()=>({ok:false,error:{code,message:'PRIVATE_SECRET'}}),()=>{throw Object.assign(Error('PRIVATE_SECRET'),{code:1,stdout:JSON.stringify({ok:false,error:{code,message:'PRIVATE_SECRET'}})});}]){
   const result=createGuard(check)({name:'pwsh'});assert.equal(typeof result,'string');assert.match(result,new RegExp(`\\[${code}\\]`));assert.doesNotMatch(result,/PRIVATE_SECRET/);
   if(code!=='auth_session_expired')assert.doesNotMatch(result,/登录已过期|重新登录/);
  }
 }
});
test('local helper failures never say that the user account expired',()=>{
 for(const [error,code] of [
  [{code:'ETIMEDOUT'},'auth_helper_timeout'],[{killed:true},'auth_helper_timeout'],
  [{code:'EACCES'},'auth_helper_start_failed'],[{code:'ENOENT'},'auth_helper_start_failed'],
  [{code:1,stdout:'PRIVATE_SECRET'},'auth_helper_invalid_response'],
  [{killed:true,code:'ERR_CHILD_PROCESS_STDIO_MAXBUFFER'},'auth_helper_invalid_response'],
  [{code:1,stdout:JSON.stringify({ok:false,error:{code:'PRIVATE_SECRET'}})},'auth_helper_invalid_response'],
  [{code:1,stdout:JSON.stringify({ok:false,error:{code:{toString:null}}})},'auth_helper_invalid_response'],
 ]){
  const result=createGuard(()=>{throw Object.assign(Error('PRIVATE_SECRET'),error);})({name:'pwsh'});
  assert.match(result,new RegExp(`\\[${code}\\]`));assert.doesNotMatch(result,/PRIVATE_SECRET|登录已过期|重新登录/);
 }
 assert.match(createGuard(()=>{JSON.parse('invalid');})({name:'pwsh'}),/\[auth_helper_invalid_response\]/);
});
test('auth errors never bypass protection-setting restrictions',()=>{
 const guard=createGuard(()=>({ok:false,error:{code:'auth_network_error'}}));
 assert.match(guard({name:'mcp__biodsh_ssh__protected_path_remove'}),/保护设置只能由用户管理/);
});
