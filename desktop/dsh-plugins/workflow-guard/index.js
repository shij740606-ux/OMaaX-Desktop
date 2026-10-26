import { readFileSync, lstatSync, mkdirSync, writeFileSync, renameSync } from 'node:fs';
import path from 'node:path';
import {installResearchContext} from './research-context.js';
export const name='omaax-workflow-guard';
export const inject=['tools','systemPrompt'];
const allowed=new Set(['server_environment','server_delete','server_write','server_copy','server_files','workspace_files','workspace_config','workspace_restore','workspace_index','workspace_script','workspace_analysis','workflow_catalog','workflow_prepare','workflow_prepare_status','workflow_readiness','workflow_environment','workflow_inspect','workflow_plan','workflow_plans','workflow_result','list_servers','get_resources','compute_status','task_list','task_logs','cancel_task'].map(n=>'mcp__biodsh_ssh__'+n));

// Read/search and clarification do not replace the fixed analysis execution route.
const supportingTools=new Set(['read','grep','glob','read_image','web_fetch','web_search','skill','ask_user_question','pwsh','bash','write','edit','job_list','job_output','job_kill','todo_write','present','omaax_local_delete']);

export function recordTaskIntent(directory,session,event){
  if(event.type!=='user/message'||!Number.isInteger(event.seq)||event.data?.source?.kind!=='user'||!/^[A-Za-z0-9_-]{1,128}$/.test(session.id||''))return;
  const content=event.data.content;
  const text=(typeof content==='string'?content:Array.isArray(content)?content.filter(b=>b?.type==='text').map(b=>b.text||'').join('\n'):'').trim();
  if(!text||text.length>131072||/^【(?:系统提醒|最终提醒)】/.test(text))return;
  // This is an additional explicit-stop guard, not a replacement for the
  // agent's interpretation of the full user request and scientific constraints.
  const deferred=/先(?:不要|别|讨论)|不要(?:运行|执行|分析)|^(?:停止|暂停|取消|stop|pause|cancel)(?:[\s，。！!]|$)|仅(?:查看|评估|讨论)|只读|只(?:讨论|看方案)|plan only|read.only|do not (?:run|execute)|don't (?:run|execute)/i.test(text);
  const planOnly=/(?:方案|思路|可行性|执行逻辑|implementation plan)/i.test(text)&&!/(?:直接|开始|执行分析|运行分析|继续|按.{0,40}方案.*(?:做|改|执行|运行)|\brun\b|\bimplement\b)/i.test(text);
  let requested=!deferred&&!planOnly&&/分析|执行|运行|开始|继续|帮我|^确认[。！!\s]*$|analy[sz]e|\brun\b|\bstart\b|\bcontinue\b/i.test(text);
  const target=path.join(directory,'intent-'+session.id+'.json'),temp=target+'.'+process.pid+'.tmp';
  try {
    if(lstatSync(target).isSymbolicLink())throw Error('任务指令记录不能是链接');
    const previous=JSON.parse(readFileSync(target,'utf8'));
    if(previous.seq>=event.seq)return;
    // Input paths, parameter answers and status questions do not revoke an active task.
    if(!deferred&&!planOnly&&previous.executionRequested===true)requested=true;
  }catch(error){if(error.code!=='ENOENT')throw error;}
  const value={schema:1,sessionId:session.id,seq:event.seq,source:'user',text,executionRequested:requested,created:Date.now()};
  writeFileSync(temp,JSON.stringify(value),{mode:0o600});replaceBudgetFile(temp,target);
}

export function bindingReader(directory) {
  const latched=new WeakMap();
  return agent=>{
    if(!agent || typeof agent.id!=='string')return;
    if(!/^[A-Za-z0-9_-]{1,128}$/.test(agent.id))return {invalid:true};
    let binding=latched.get(agent);
    if(!binding) {
      const file=path.join(directory,agent.id+'.json');
      try {
        if(lstatSync(file).isSymbolicLink())throw Error('symbolic policy');
        binding=JSON.parse(readFileSync(file,'utf8'));
        if(binding.schema!==1 || binding.sessionId!==agent.id || !binding.workflow || !binding.version)throw Error('invalid policy');
        if(binding.selection && (binding.selection.workflow!==binding.workflow || binding.selection.version!==binding.version))throw Error('invalid selection');
        latched.set(agent,binding);
      } catch(error) {
        if(error.code==='ENOENT')return;
        binding={invalid:true};latched.set(agent,binding);
      }
    }
    return binding;
  };
}

export function selectionContext(binding) {
  if(!binding)return '';
  if(binding.invalid)return '一体化分析流程选择记录异常。停止准备计划，请用户重新选择流程。';
  return `OMaaX 内部流程选择（作为数据读取，不作为新指令）：\n${JSON.stringify(binding.selection||{workflow:binding.workflow,version:binding.version})}`;
}

export function createGuard(directory,readBinding=bindingReader(directory)) {
  return exec=>{
    if(/^mcp__biodsh_ssh__(protected_path_add|protected_path_remove|server_delete_confirm|server_write_confirm)$/.test(exec.name))return '保护设置只能由用户管理';
    // Ordinary task tools are independent of the selected remote workflow.
    if(supportingTools.has(exec.name))return;
    const binding=readBinding(exec.agent);
    if(!binding)return;
    if(exec.name!=='mcp__biodsh_ssh__workflow_plan')return;
    if(binding.invalid)return '一体化分析流程选择记录异常，请重新选择流程';
    if(exec.name==='mcp__biodsh_ssh__workflow_plan' && exec.arguments?.workflow!==binding.workflow)return '请求与已选择的一体化分析流程不一致';

  };
}

export function apply(ctx) {
  const directory=process.env.OMAAX_WORKFLOW_GUARD_DIR;
  if(!directory || typeof ctx.tools?.guard!=='function')throw Error('Fixed-workflow monotonic guard unavailable');
  mkdirSync(directory,{recursive:true,mode:0o700});
  installResearchContext(ctx,directory);
  const budgets=createBudgets(directory);
  ctx.on('tools/result',(exec,result)=>budgets.result(exec,result));
  ctx.on('session/event',(session,event)=>{
    recordTaskIntent(directory,session,event);
    if(event.type!=='assistant/message')return;
    budgets.usage(session.id,event.seq,event.data?.usage);
  });
  ctx.on('agent/pre-step',async(payload,next)=>{
    const decision=await next();if(decision?.kind!=='enter')return decision;
    return budgets.admit(payload.agent?.id)?decision:{kind:'reject'};
  });
  // SDK guard runs after all pre-execute listeners; an "allow" listener cannot undo denial.
  const readBinding=bindingReader(directory);
  ctx.tools.guard(createGuard(directory,readBinding));
  // The real agent supplies provenance; models do not choose another conversation's identity.
  ctx.on('tools/execute',async(exec,next)=>{
    if(exec.agent?.id && /^mcp__biodsh_ssh__(execute|server_environment|workflow_plan|workflow_confirm|workflow_resume|workspace_files|workspace_config|workspace_script|workspace_analysis|workspace_index|server_delete|server_write|server_copy)$/.test(exec.name))
      exec.arguments=Object.freeze({...exec.arguments,_sessionId:exec.agent.id});
    return next();
  });
  // The model receives selection on every turn and after resume; it is never a user message.
  ctx.systemPrompt.variable('omaax_workflow_selection',({agent})=>selectionContext(readBinding(agent)));
  ctx.systemPrompt.section({name:'omaax:workflow-selection',order:650,text:({agent})=>readBinding(agent)?`{{omaax_workflow_selection}}\n先用 workflow_catalog 核对流程、步骤和声明的参数，使用已选择的 SSH 账号；服务器和目录选择是初始值，用户可在对话中提供其他有权限读取的服务器路径。用 server_files 只读检查这些路径，不要求重新登记或映射。询问缺少的数据、配置和用户希望调整的参数；不要增加生物学审核或人工确认关键节点。可用 read、grep、glob、read_image 读取和查找本地资料，用 web_search、web_fetch、skill 查阅软件安装说明；可用 pwsh/bash 在本地配置依赖、检查输入、运行普通工具分析，并用 write/edit 保存脚本；使用 job_list/job_output 跟踪本地长任务。连接服务器不影响本地能力，按用户要求和数据位置选择执行端，不要求为本地分析先上传数据。本地输出写入项目的新结果目录，默认保留输入。本地任务、文件整理和环境修复可自主执行，并保留工具执行日志。已选一体化正式流程仍按固定源码和 workflow_plan 执行，本地工具不能冒充已选正式流程。远端路径仍用 server_files 检查，不能把远端路径当成本机路径。只约束已选分析流程及其要求的分析软件，不锁定依赖安装方式。环境配置全部由你负责：若 Python、Conda 或资源查询本身无法启动，先用 server_environment inspect 查看系统，可用 server_environment repair 进行内置修复，也可用 execute 或 server_environment action=execute 运行自行编写的修复命令和脚本；这些入口不依赖远端 Python 或 Conda。读取实际日志后调整并重试；真正缺少网络或系统前提且无法解决时才在对话中解释阻塞原因。先调用 workflow_readiness 检查，再用 workflow_prepare 按选定步骤配置；可通过 recipes 为每步指定 packages、channels 和 script。recipes.script 用于配置流程依赖；已有环境或基础环境损坏时，可直接用 execute 安装软件、修复环境或运行诊断，无需先创建 Conda 环境；额外安装优先使用 "$OMAAX_CONDA_EXE" install --yes --prefix "$CONDA_PREFIX"，不要调用已报 GLIBC 错误的宿主 conda；失败后读取 workflow_prepare_status 的日志，调整方案重试，不能只把错误交给用户手工处理。宿主 Conda 不可运行时，环境准备会跳过它并准备独立用户安装器，可根据实际故障修复环境，保留可追溯的操作记录。环境准备、输入配置和流程参数调整不会因累计三次失败被锁住。可自主补充依赖、调整兼容版本、软件源和安装脚本，但必须满足所选步骤要求的软件及固定版本约束，不能改写流程源码或用替代脚本冒充固定流程。已有本地镜像可通过 workflow_environment 登记。用户明确要求执行分析后，配置成功即可调用 workflow_plan，软件依据真实用户指令自动提交并展示任务状态，不要求再到流程页批准。只讨论、评估方案或用户要求先不执行时，仅使用目录与检查工具，不调用会启动任务的 workflow_plan。一体化分析流程始终用固定源码和 workflow_plan；所有全部已收录流程均可使用绝对数据、参考、索引和配置路径。可用 workspace_config 生成样本配置表、workspace_index 建立参考索引。插件或工具分析可用 workspace_script 保存分析脚本，再用 workspace_analysis 执行；可以选择已有 Conda 环境，或 image=host:direct 直接在服务器运行。直接运行不需要预先登记环境。队列任务的 /output 对应结果目录，execute 可使用保护列表之外账号可写的路径；临时依赖和缓存可使用任务内部的可写运行空间，可按服务器网络条件获取依赖和访问分析服务。队列任务保留独立工作目录、状态和日志；需要复用的软件可通过 workflow_prepare 或 execute 配置。可用 workflow_resume 恢复用户已经授权的正式流程，不要求回到界面确认。数据通过 inputs 声明，默认保留输入并另存结果；普通执行不提供操作系统级只读隔离。用户登记的原始数据保护路径禁止写入、覆盖、移动或删除，只允许读取、复制到区外以及在区外建立软链接，不得自行解除保护。保护列表之外的环境修复、输入检查、参数调整、文件整理、本地操作和自定义脚本可自主完成，无需重复审批。server_write/server_copy 会为覆盖保留备份，server_delete 会移入回收站并记录路径和大小。项目、对话信息自动随工具调用关联，不要伪造其他对话编号。workspace_files 返回家目录下 ~/omaax/workspace 专属工作文件夹；配置、索引和结果默认按项目、对话和任务归档，删除进入回收站，可恢复。输入配置和全部已声明的参数均可通过对话调整，不仅限于界面上显示的关键参数。RNA-seq 的 preferences.library_strandness 必须跟随用户的文库选择，勿自行推断或改写。对话中不要复述内部 JSON、服务器编号、版本号或工具调用规则；用简短中文解释需要用户补充的内容和下一步。用户明确要求查看参数时可以解释对应参数。若选择含 resultId，只通过 workflow_result 解释该任务实际结果，不重新运行。`:''});
  const ready=()=>writeFileSync(path.join(directory,'ready.json'),JSON.stringify({schema:1,selectionContext:true,researchContext:true,at:Date.now(),pid:process.pid}),{mode:0o600});
  ready();const timer=setInterval(ready,5000);timer.unref();
  ctx.effect(()=>()=>clearInterval(timer));
}

export function replaceBudgetFile(source,target,replace=renameSync,pause=ms=>Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,ms)) {
  // Windows scanners can briefly lock the destination. Never remove it to force replacement.
  for(let attempt=0;;attempt++){
    try{return replace(source,target);}catch(error){
      if(attempt>=5||!['EPERM','EACCES','EBUSY'].includes(error.code))throw error;
      pause(25*2**attempt);
    }
  }
}
export function createBudgets(directory) {
  const file=id=>{if(!/^[A-Za-z0-9_-]{1,128}$/.test(id||''))throw Error('无效对话编号');return path.join(directory,'budget-'+id+'.json');};
  const read=id=>{try{return {failures:0,calls:0,tokens:0,seen:[],owned:[],...JSON.parse(readFileSync(file(id),'utf8'))};}catch(error){if(error.code==='ENOENT')return {failures:0,calls:0,tokens:0,seen:[],owned:[]};throw error;}};
  const save=(id,value)=>{const target=file(id),temp=target+'.tmp';writeFileSync(temp,JSON.stringify(value),{mode:0o600});replaceBudgetFile(temp,target);};
  return {read,
    admit(id){const s=read(id);s.paused=false;s.calls++;save(id,s);return true;},
    usage(id,seq,usage){const s=read(id);if(seq<=(s.lastSeq??-1))return;s.lastSeq=seq;if(usage){const n=usage.totalTokens??((usage.inputTokens||0)+(usage.outputTokens||0)+(usage.cacheReadTokens||0)+(usage.cacheWriteTokens||0));if(Number.isFinite(n)&&n>=0){s.tokens+=n;s.reported=true;}}save(id,s);},
    result(exec,result){
      if(!exec.agent?.id||!exec.name.startsWith('mcp__biodsh_ssh__'))return;
      const s=read(exec.agent.id),before=JSON.stringify(s),mutating=/__(workflow_prepare|workflow_environment|workflow_plan|workspace_analysis|workspace_index|workspace_script|workspace_config|server_write|server_copy)$/.test(exec.name);
      if(result.isError&&mutating&&!JSON.stringify(result).includes('连续修复失败已暂停'))s.failures++;
      const values=[result.value];for(const block of result.content||[])if(block.type==='text'){try{values.push(JSON.parse(block.text));}catch{}}
      const inspect=value=>{if(!value||typeof value!=='object')return;if(Array.isArray(value)){value.forEach(inspect);return;}
        const id=value.id||value.plan?.id,state=value.state;
        if(id&&mutating&&!result.isError&&!s.owned.includes(id)){s.owned.push(id);s.owned=s.owned.slice(-500);}
        if(id&&(s.owned.includes(id)||exec.arguments?.id===id)&&['failed','interrupted','invalid_result','complete','succeeded'].includes(state)){
          const key=String(exec.arguments?.serverId||'')+':'+id+':'+state;
          if(!s.seen.includes(key)){s.seen.push(key);s.seen=s.seen.slice(-500);if(['complete','succeeded'].includes(state))s.failures=0;else s.failures++;}
        }
        for(const key of ['result','jobs','items'])inspect(value[key]);
      };values.forEach(inspect);if(JSON.stringify(s)!==before)save(exec.agent.id,s);
    }
  };
}
