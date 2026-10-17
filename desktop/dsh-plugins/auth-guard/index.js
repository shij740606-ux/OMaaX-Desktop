import {writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
export const name='omaax-auth-guard';
export const inject=['tools'];
const names=new Set('workflow_catalog workflow_inspect workflow_readiness workflow_prepare workflow_prepare_status workflow_environment workflow_plan workflow_plans workflow_result server_delete server_write server_copy server_files workspace_files workspace_config workspace_restore workspace_index workspace_script workspace_analysis list_servers get_resources compute_status task_list task_logs cancel_task'.split(' ').map(n=>'mcp__biodsh_ssh__'+n));
export function createGuard(check) {
  return exec=>{
    // Local shell, filesystem, arbitrary MCP and approval tools bypass account/deletion boundaries.
    if(!names.has(exec.name))return '账号保护：请使用受控服务器工具；不可用终端、其他 MCP 或本地文件工具绕过权限与删除限制';
    try {if(check()?.ok===true)return;}catch{}
    return '账号未登录、已过期或没有分析权限，请在 OMaaX 登录并联系管理员授权';
  };
}
export function apply(ctx) {
  const executable=process.env.OMAAX_AUTH_EXECUTABLE;
  if(!executable||!process.env.OMAAX_AUTH_GUARD_FILE||!process.env.OMAAX_AUTH_GUARD_NONCE||!ctx.tools?.guard)throw Error('Mandatory account guard unavailable');
  ctx.tools.guard(createGuard(()=>{
    const stdout=execFileSync(executable,['--auth-check'],{timeout:30000,windowsHide:true,maxBuffer:4096,encoding:'utf8'});
    return JSON.parse(stdout);
  }));
  writeFileSync(process.env.OMAAX_AUTH_GUARD_FILE,process.env.OMAAX_AUTH_GUARD_NONCE,{mode:0o600});
}
