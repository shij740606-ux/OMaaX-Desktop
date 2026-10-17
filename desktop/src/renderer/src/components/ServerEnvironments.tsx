import {useApp} from '../store';
import {serverError} from '../serverErrors';
import {useEffect,useRef,useState} from 'react';
import catalog from '../../../../resources/workflows/catalog.json';
import {connectionRevision,prepareServer,useServerConnections,type Setup} from '../serverConnections';
import {orderedSteps,readinessLabels,type StepReadiness} from '../views/workflowPresentation';
import SudoInstall from './SudoInstall';

type Image={id:string;tags:string[];size:number|null;installed:boolean;approved:boolean;bindings:{workflow:string;default:boolean;steps:string[]}[];activeJobs:number};
type Inventory={images:Image[];error:string;setup:Setup;checkedAt:number;conda?:{environments:{name:string;path:string}[];error:string}};
type Task={id?:string;state:string;detail:string;log?:string;request?:{steps:string[]}};
const cache=new Map<string,Inventory>();
type Step={id:string;name:string;entryPresent:boolean};
const flows=catalog.workflows as {id:string;name:string;nodes:Step[];extraNodes:Step[];order:string[]}[];
const message=(reason:unknown)=>serverError(reason,'environment');
const size=(bytes:number|null)=>bytes===null?'大小未知':bytes>=1073741824?(bytes/1073741824).toFixed(2)+' GiB':(bytes/1048576).toFixed(1)+' MiB';

export default function ServerEnvironments({server,label,refreshToken=0}:{server:string;label:string;refreshToken?:number}){
 const {setTab}=useApp();
 const connections=useServerConnections(),revision=connectionRevision(server),key=server+'@'+revision;
 const [inventory,setInventory]=useState<Inventory|null>(()=>cache.get(key)||null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [workflow,setWorkflow]=useState(()=>sessionStorage.getItem('omaax.environment.workflow.'+server)||flows[0].id);
 const flow=flows.find(f=>f.id===workflow)||flows[0],steps=orderedSteps(flow);
 const selectionKey='omaax.environment.steps.'+server+'.'+flow.id;
 const [selected,setSelected]=useState<string[]>([]),[checks,setChecks]=useState<Record<string,StepReadiness>>({}),[busy,setBusy]=useState(false);
 const [bootstrapImage,setBootstrapImage]=useState('');
 const [task,setTask]=useState<Task>({state:'idle',detail:'勾选需要的步骤后再检查或配置，不会自动安装其他工具。'});
 const current=useRef('');current.current=key+'|'+flow.id;const request=useRef(0);
 const call=<T,>(action:string,body:unknown={})=>window.biodsh.personalCompute(server,action,body) as Promise<T>;
 const load=async()=>{
  const expected=key,token=++request.current;setLoading(true);setError('');
  try{const result=await call<Inventory>('server_environments',{conda:true});if(current.current.startsWith(expected+'|')&&token===request.current){cache.set(key,result);setInventory(result);}}
  catch(e){if(current.current.startsWith(expected+'|')&&token===request.current)setError(message(e));}
  finally{if(token===request.current)setLoading(false);}
 };
 useEffect(()=>{const saved=cache.get(key);setInventory(saved||null);if(refreshToken||!saved||Date.now()/1000-saved.checkedAt>30)void load();return()=>{request.current++;};},[key,refreshToken]);
 useEffect(()=>{sessionStorage.setItem('omaax.environment.workflow.'+server,flow.id);let saved:unknown=[];try{saved=JSON.parse(sessionStorage.getItem(selectionKey)||'[]');}catch{}setSelected(Array.isArray(saved)?saved.filter((id):id is string=>typeof id==='string'&&steps.some(s=>s.id===id&&s.entryPresent)):[]);setChecks({});setNotice('');setError('');},[selectionKey]);
 const choose=(next:string[])=>{setSelected(next);sessionStorage.setItem(selectionKey,JSON.stringify(next));setNotice('');};
 const inProgress=['queued','running'].includes(task.state),setup=connections[server]?.setup||inventory?.setup;
 useEffect(()=>{
  let disposed=false,inFlight=false;const expected=key+'|'+flow.id;
  const poll=async()=>{if(inFlight)return;inFlight=true;try{const result=await call<Task>('workflow_prepare_status',{workflow:flow.id});if(!disposed&&current.current===expected){setTask(result);if(result.state==='complete'&&result.id&&result.id!==finished.current){finished.current=result.id;setChecks({});void load();}}}catch(e){if(!disposed&&current.current===expected)setError(message(e));}finally{inFlight=false;}};
  setTask({state:'idle',detail:'勾选需要的步骤后再检查或配置，不会自动安装其他工具。'});void poll();
  const timer=setInterval(()=>void poll(),5000);return()=>{disposed=true;clearInterval(timer);};
 },[key,flow.id]);
 const finished=useRef('');
 const run=async(work:()=>Promise<void>)=>{const expected=current.current;setBusy(true);setError('');setNotice('');try{await work();}catch(e){if(current.current===expected)setError(message(e));}finally{if(current.current===expected)setBusy(false);}};
 const check=async()=>{const expected=current.current;const result=await call<{steps:Record<string,StepReadiness>}>('workflow_readiness',{workflow:flow.id,steps:selected,parameters:{},configs:{}});if(current.current===expected){setChecks(result.steps);setNotice('软件检查完成；参考数据和输入配置在生成分析计划时检查。');}};
 const summary=(item:Image)=>item.bindings.map(binding=>{const f=flows.find(f=>f.id===binding.workflow);return (f?.name||binding.workflow)+(binding.steps.length?' · '+binding.steps.length+' 个步骤':binding.default?' · 默认环境':'');}).join('；');
 return <div className="flex flex-col gap-4 min-w-0">
  <section className="card p-5 flex flex-col gap-3"><div className="flex items-center justify-between gap-3"><h2 className="t-headline">服务器基础环境</h2><span className="t-caption">{setup?.state==='complete'?'已就绪':setup?.state==='running'?'配置中':'待检查'}</span></div><p className="t-caption">提供服务器执行组件；分析软件只按你选中的步骤配置。</p><p role="status">{setup?(['failed','interrupted'].includes(setup.state)?message(setup.errorDetail||setup.detail):setup.detail):'尚未查询基础环境状态'}</p><button className="btn btn-tint self-start" disabled={busy||setup?.state==='running'} onClick={()=>void run(async()=>{await prepareServer(server,true,bootstrapImage.trim()||undefined);await load();})}>{setup?.state==='running'?'正在准备…':'检查并准备基础环境'}</button>{setup?.errorDetail&&<details><summary className="t-caption cursor-pointer">基础环境技术详情</summary><pre className="whitespace-pre-wrap break-all t-caption mt-2">{setup.errorDetail}</pre></details>}<details><summary className="t-caption cursor-pointer">基础环境高级设置</summary><label className="block mt-2">自定义基础运行镜像<input className="field mt-1" value={bootstrapImage} disabled={busy||setup?.state==='running'} onChange={e=>setBootstrapImage(e.target.value)} placeholder="留空使用默认环境"/></label><p className="t-caption mt-2">系统组件需要管理员权限时，可在这里授权安装。</p><SudoInstall id={server} label={label} image={bootstrapImage.trim()||undefined}/></details></section>
  {loading&&<p role="status">正在读取服务器环境…</p>}{error&&<p role="alert" className="break-words" style={{color:'var(--red)'}}>{error}</p>}
  <section className="card p-5 flex flex-col gap-4"><div><h2 className="t-headline">OMaaX 分析环境</h2><p className="t-caption mt-1">查看已登记环境、占用空间及关联流程。环境大小包含共享层，不能直接相加计算实际磁盘占用。</p></div>
   {inventory?.error&&<p role="alert" className="break-words" style={{color:'var(--orange)'}}>环境清单读取未完成：{message(inventory.error)}</p>}
   {!loading&&inventory&&!inventory.images.length&&<p className="t-caption">尚未发现 OMaaX 分析环境，可在下面按步骤配置。</p>}
   <div className="max-h-80 overflow-y-auto flex flex-col gap-3">{inventory?.images.map((item,index)=><article className="hairline-b pb-3 min-w-0" key={item.id}><div className="flex flex-wrap items-center gap-3"><strong>{summary(item)||'分析环境 '+(index+1)}</strong><span className="t-caption">{item.installed?'已安装':'服务器环境缺失'} · {size(item.size)}{item.activeJobs?' · '+item.activeJobs+' 个任务正在使用':''}</span></div><details className="t-caption mt-2"><summary>环境详情</summary><div className="break-all selectable mt-2">{item.id}</div><div className="break-all">{item.tags.join('、')}</div><p>{item.approved?'已登记可供分析使用':'尚未关联分析流程'}</p></details></article>)}</div>
  </section>
  <section className="card p-5 flex flex-col gap-4"><div><h2 className="t-headline">按需配置分析软件</h2><p className="t-caption mt-1">这里查看软件检查结果和配置日志。安装、依赖调整与报错修复交给流程对话中的模型。</p></div>
   <label>分析流程<select aria-label="管理环境的分析流程" className="field mt-1" value={flow.id} disabled={busy} onChange={e=>setWorkflow(e.target.value)}>{flows.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
   <fieldset disabled={busy||inProgress} className="workflow-step-selection max-h-96 overflow-y-auto"><legend className="t-caption mb-2">需要配置的步骤（已选 {selected.length} 项）</legend>{steps.map(step=><label key={step.id}><input type="checkbox" aria-label={step.name} checked={selected.includes(step.id)} disabled={!step.entryPresent} onChange={e=>choose(e.target.checked?[...selected,step.id]:selected.filter(id=>id!==step.id))}/><span>{step.name}<small className="block t-caption">{!step.entryPresent?'暂不可用':checks[step.id]?readinessLabels[checks[step.id].state]:'尚未检查'}</small>{selected.includes(step.id)&&checks[step.id]?.detail&&<small className="block t-caption break-words">{checks[step.id].detail}</small>}</span></label>)}</fieldset>
   <div className="flex flex-wrap gap-2"><button className="btn btn-tint" disabled={busy||inProgress||!selected.length} onClick={()=>void run(check)}>检查所需软件</button><button className="btn btn-primary" disabled={busy||!selected.length} onClick={()=>{localStorage.setItem('omaax.workflow.view',flow.id);localStorage.setItem('omaax.workflow.host',server);sessionStorage.setItem('omaax.environment.handoff',JSON.stringify({workflow:flow.id,server,steps:selected}));setTab('workflows');}}>在对话中配置环境</button></div>
   <div role="status" className="workflow-environment-status"><strong>{inProgress?'配置进行中':task.state==='failed'?'配置未完成':task.state==='complete'?'最近一次配置已完成':'配置记录'}</strong><p>{task.state==='failed'?message(task.detail):task.detail}</p>{task.request?.steps&&<p className="t-caption">本记录包含：{task.request.steps.map(id=>steps.find(s=>s.id===id)?.name||id).join('、')}</p>}{task.log&&<details><summary>查看安装日志</summary><pre className="whitespace-pre-wrap break-all selectable mt-2">{task.log}</pre></details>}</div>

   {notice&&<p role="status" className="t-caption">{notice}</p>}
  </section>
  <section className="card p-5 flex flex-col gap-3"><h2 className="t-headline">账号已有的 Conda 环境</h2><p className="t-caption">读取账号登记记录和常见安装位置，只查看名称和路径，不改动已有环境。未登记的自定义位置可能不在列表中。</p>{inventory?.conda?.error&&<p className="t-caption break-words">{message(inventory.conda.error)}</p>}<div className="max-h-80 overflow-y-auto flex flex-col gap-3">{inventory?.conda?.environments.map(env=><div className="hairline-b pb-2" key={env.path}><strong>{env.name}</strong><div className="t-caption selectable break-all">{env.path}</div></div>)}</div>{inventory?.conda&&!inventory.conda.error&&!inventory.conda.environments.length&&<p className="t-caption">未发现已有的 Conda 环境。</p>}</section>
 </div>;
}
