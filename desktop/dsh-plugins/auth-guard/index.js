import {writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {createImageTool} from './image.js';
import {createLocalDeleteTool,localExecutionGuard} from './local.js';
export const name='omaax-auth-guard';
export const inject=['tools','approval'];
const names=new Set('server_environment workflow_catalog workflow_inspect workflow_readiness workflow_prepare workflow_prepare_status workflow_environment workflow_plan workflow_plans workflow_result server_delete server_write server_copy server_files workspace_files workspace_config workspace_restore workspace_index workspace_script workspace_analysis list_servers get_resources compute_status task_list task_logs cancel_task'.split(' ').map(n=>'mcp__biodsh_ssh__'+n));
const supportingTools=new Set(['read','grep','glob','read_image','web_fetch','web_search','skill','ask_user_question','pwsh','bash','write','edit','job_list','job_output','job_kill','todo_write','present','omaax_local_delete']);
const authMessages={
  auth_session_expired:'账号未登录或登录已过期，请在 OMaaX 重新登录。',
  auth_forbidden:'当前账号没有使用此工具的权限，请联系管理员。',
  auth_network_error:'暂时无法连接账号验证服务，请检查网络后重试。',
  auth_timeout:'账号验证超时，请稍后重试。',
  auth_service_unavailable:'账号验证服务暂不可用，请稍后重试。',
  auth_invalid_response:'账号验证服务返回异常，请稍后重试。',
  auth_local_error:'无法读取本机账号状态，请重启软件后重试。',
  auth_helper_timeout:'本机账号校验超时，请稍后重试。',
  auth_helper_start_failed:'无法启动本机账号校验，请重启软件后重试。',
  auth_helper_invalid_response:'本机账号校验返回异常，请重启软件后重试。',
};
function guardFailure(error){
  let value=error;
  if(typeof error?.stdout==='string'){try{value=JSON.parse(error.stdout);}catch{value=null;}}
  const known=typeof value?.error?.code==='string'&&Object.hasOwn(authMessages,value.error.code);
  let code=known?value.error.code:'auth_local_error';
  if(error?.code==='ERR_CHILD_PROCESS_STDIO_MAXBUFFER')code='auth_helper_invalid_response';
  else if(error?.code==='ETIMEDOUT'||error?.killed)code='auth_helper_timeout';
  else if(['ENOENT','EACCES','EPERM'].includes(error?.code))code='auth_helper_start_failed';
  else if(error instanceof SyntaxError||(typeof error?.code==='number'&&!known))code='auth_helper_invalid_response';
  return `${authMessages[code]} [${code}]`;
}
export function createGuard(check, externalAllowed=()=>false) {
  return exec=>{
    if(/^mcp__biodsh_ssh__(protected_path_add|protected_path_remove|server_delete_confirm|server_delete_notified|server_write_confirm)$/.test(exec.name))return '保护设置只能由用户管理';
    try {const result=check();if(result?.ok===true)return;return guardFailure(result);}catch(error){return guardFailure(error);}
  };
}
export function externalPolicy(servers, approval) {
  const prefixes=servers.filter(n=>/^[A-Za-z0-9_-]{1,32}$/.test(n)&&!n.includes('__')&&!/^(biodsh|omaax)/.test(n)).map(n=>`mcp__${n}__`);
  const approved=new WeakSet();
  return {
    allowed:exec=>approved.has(exec),
    async before(exec,next) {
      if(!prefixes.some(prefix=>exec.name.startsWith(prefix)))return next();
      if(exec.signal?.aborted)return {kind:'deny',reason:'操作已取消'};
      const decision=await next();
      if(decision.kind!=='allow')return decision;
      approved.add(exec);return decision;
    }
  };
}
export function apply(ctx) {
  const executable=process.env.OMAAX_AUTH_EXECUTABLE;
  if(!executable||!process.env.OMAAX_AUTH_GUARD_FILE||!process.env.OMAAX_AUTH_GUARD_NONCE||!ctx.tools?.guard)throw Error('Mandatory account guard unavailable');
  const external=externalPolicy(JSON.parse(process.env.OMAAX_EXTERNAL_MCP||'[]'),ctx.approval);
  ctx.on('tools/pre-execute',external.before);
  ctx.tools.register(createImageTool());
  ctx.tools.register(createLocalDeleteTool(ctx.approval));
  ctx.tools.guard(createGuard(()=>{
    const stdout=execFileSync(executable,['--auth-check'],{timeout:30000,windowsHide:true,maxBuffer:4096,encoding:'utf8'});
    return JSON.parse(stdout);
  },external.allowed));
  writeFileSync(process.env.OMAAX_AUTH_GUARD_FILE,process.env.OMAAX_AUTH_GUARD_NONCE,{mode:0o600});
}
