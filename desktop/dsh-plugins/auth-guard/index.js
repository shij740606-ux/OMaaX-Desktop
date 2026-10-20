import {writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {createImageTool} from './image.js';
export const name='omaax-auth-guard';
export const inject=['tools','approval'];
const names=new Set('workflow_catalog workflow_inspect workflow_readiness workflow_prepare workflow_prepare_status workflow_environment workflow_plan workflow_plans workflow_result server_delete server_write server_copy server_files workspace_files workspace_config workspace_restore workspace_index workspace_script workspace_analysis list_servers get_resources compute_status task_list task_logs cancel_task'.split(' ').map(n=>'mcp__biodsh_ssh__'+n));
const supportingTools=new Set(['read','grep','glob','read_image','web_fetch','web_search','skill','ask_user_question']);
export function createGuard(check, externalAllowed=()=>false) {
  return exec=>{
    // Reads and research still require login; arbitrary execution and writes retain their protection.
    if(!names.has(exec.name)&&!supportingTools.has(exec.name)&&exec.name!=='omaax_generate_image'&&!externalAllowed(exec))return '账号保护：请使用受控服务器工具；外部工具需逐次确认，不可绕过权限与删除限制';
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
      const decision=await next();
      if(decision.kind==='deny')return decision;
      try {
        const outcome=await approval?.request({agent:exec.agent,toolName:exec.name,callId:exec.callId,signal:exec.signal,
          reason:'外部工具可能修改或删除数据。请核对本次工具及参数，只在确认操作范围后允许；不得用它绕过服务器删除确认。'});
        if(outcome==='allowed-once'&&!exec.signal?.aborted){approved.add(exec);return {kind:'allow'};}
      } catch {}
      return {kind:'deny',reason:'本次外部工具调用未获确认，未执行'};
    }
  };
}
export function apply(ctx) {
  const executable=process.env.OMAAX_AUTH_EXECUTABLE;
  if(!executable||!process.env.OMAAX_AUTH_GUARD_FILE||!process.env.OMAAX_AUTH_GUARD_NONCE||!ctx.tools?.guard)throw Error('Mandatory account guard unavailable');
  const external=externalPolicy(JSON.parse(process.env.OMAAX_EXTERNAL_MCP||'[]'),ctx.approval);
  ctx.on('tools/pre-execute',external.before);
  ctx.tools.register(createImageTool());
  ctx.tools.guard(createGuard(()=>{
    const stdout=execFileSync(executable,['--auth-check'],{timeout:30000,windowsHide:true,maxBuffer:4096,encoding:'utf8'});
    return JSON.parse(stdout);
  },external.allowed));
  writeFileSync(process.env.OMAAX_AUTH_GUARD_FILE,process.env.OMAAX_AUTH_GUARD_NONCE,{mode:0o600});
}
