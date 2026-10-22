import {writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {createImageTool} from './image.js';
import {createLocalDeleteTool,localExecutionGuard} from './local.js';
export const name='omaax-auth-guard';
export const inject=['tools','approval'];
const names=new Set('server_environment workflow_catalog workflow_inspect workflow_readiness workflow_prepare workflow_prepare_status workflow_environment workflow_plan workflow_plans workflow_result server_delete server_write server_copy server_files workspace_files workspace_config workspace_restore workspace_index workspace_script workspace_analysis list_servers get_resources compute_status task_list task_logs cancel_task'.split(' ').map(n=>'mcp__biodsh_ssh__'+n));
const supportingTools=new Set(['read','grep','glob','read_image','web_fetch','web_search','skill','ask_user_question','pwsh','bash','write','edit','job_list','job_output','job_kill','todo_write','present','omaax_local_delete']);
export function createGuard(check, externalAllowed=()=>false) {
  return exec=>{
    if(/^mcp__biodsh_ssh__(protected_path_add|protected_path_remove|server_delete_confirm|server_delete_notified|server_write_confirm)$/.test(exec.name))return '保护设置只能由用户管理';
    try {if(check()?.ok===true)return;}catch{}
    return '账号未登录或登录已过期，请在 OMaaX 重新登录';
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
      if(decision.kind==='deny')return decision;
      approved.add(exec);return {kind:'allow'};
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
