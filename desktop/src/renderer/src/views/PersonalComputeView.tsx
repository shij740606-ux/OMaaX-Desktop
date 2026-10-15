import { useEffect, useRef, useState } from 'react';
import type { SshHost, SshMetrics } from '@shared/types';
import { ResourceSummary } from './HostsView';
import ServerWriteRequests from '../components/ServerWriteRequests';
import ServerWorkspaceFiles from '../components/ServerWorkspaceFiles';
import SudoInstall from '../components/SudoInstall';
import {analysisContext,type AnalysisContext} from './analysisContext';
import { useApp } from '../store';
const jobLabels:Record<string,string>={queued:'等待资源',running:'分析中',succeeded:'已完成',complete:'已完成',partial:'部分完成',invalid_result:'结果不满足要求',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
const auditLabels:Record<string,string>={'bootstrap.shared_ready':'共享文件夹已准备','bootstrap.install_packages':'安装计算环境','bootstrap.image_ready':'运行环境已准备','bootstrap.ready':'环境检查完成',configure:'更新保护设置','job.cancel_requested':'请求取消任务','data.export':'导出数据','data.import_pending':'开始接收数据','data.import_complete':'数据接收完成'};

type Status = {uid:number; root:string; protection:unknown; error:string; sources:Record<string,string>; images:string[]};
type Job = {id:string; state:string; detail:string; created:number; spec:{argv:string[]; sources:string[];title?:string;context?:AnalysisContext}};
type Audit = {id:number; at:number; action:string; detail:string};
const call = <T,>(id:string,action:string,body:unknown={}) => window.biodsh.personalCompute(id,action,body) as Promise<T>;

export default function PersonalComputeView({section}:{section:'resources'|'tasks'|'data'|'protection'}) {
  const {setTab,currentSession}=useApp();
  const [hosts,setHosts]=useState<SshHost[]>([]), [id,setId]=useState('');
  const currentId=useRef(id);currentId.current=id;
  const [status,setStatus]=useState<Status|null>(null), [metric,setMetric]=useState<SshMetrics|null>(null);
  const [jobs,setJobs]=useState<Job[]>([]), [audit,setAudit]=useState<Audit[]>([]);
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [notice,setNotice]=useState('');
  const [candidates,setCandidates]=useState<string[]>([]), [image,setImage]=useState('');
  const [argv,setArgv]=useState('');
  const [sources,setSources]=useState<string[]>([]), [cpu,setCpu]=useState(20), [memory,setMemory]=useState(51200), [timeout,setTimeoutValue]=useState(86400);
  const [log,setLog]=useState(''), [sourceText,setSourceText]=useState(''), [imageText,setImageText]=useState('');
  const [source,setSource]=useState(''), [relative,setRelative]=useState(''), [target,setTarget]=useState('');
  const [jobId,setJobId]=useState(()=>crypto.randomUUID());
  const [bootstrapImage,setBootstrapImage]=useState('');
  const run=async(work:()=>Promise<void>)=>{setBusy(true);setError('');setNotice('');try{await work();}catch(e){setError(String(e));}finally{setBusy(false);}};
  useEffect(()=>{void window.biodsh.sshHosts().then(rows=>{const trusted=rows.filter(h=>h.hostKey);setHosts(trusted);setId(trusted.find(h=>{try{return h.id===JSON.parse(sessionStorage.getItem('omaax.files.open')||'{}').serverId;}catch{return false;}})?.id||trusted[0]?.id||'');setCandidates(trusted.map(h=>h.id));}).catch(e=>setError(String(e)));},[]);
  const refresh=async()=>{
    if(!id)return;
    if(section==='resources'){const next=await window.biodsh.sshHostMetrics(id);if(currentId.current===id)setMetric(next);return;}
    const next=await call<Status>(id,'status');if(currentId.current!==id)return;setStatus(next);
    if(section==='tasks'){const rows=await call<Job[]>(id,'jobs');if(currentId.current===id)setJobs(rows);}
    if(section==='protection'){const rows=await call<Audit[]>(id,'audit');if(currentId.current===id)setAudit(rows);}
  };
  useEffect(()=>{setStatus(null);setMetric(null);setJobs([]);setLog('');setSources([]);setSource('');setImage('');void run(refresh);},[id,section]);
  useEffect(()=>{const finished=(event:Event)=>{if((event as CustomEvent<{id:string}>).detail.id===id)void refresh().catch(e=>setError(String(e)));};window.addEventListener('biodsh-install-finished',finished);return()=>window.removeEventListener('biodsh-install-finished',finished);},[id,section]);
  useEffect(()=>{if(!status)return;setSourceText(Object.entries(status.sources).map(([k,v])=>`${k}=${v}`).join('\n'));setImageText(status.images.join('\n'));setImage(old=>status.images.includes(old)?old:status.images[0]||'');setSource(old=>old in status.sources?old:Object.keys(status.sources)[0]||'');},[status]);
  useEffect(()=>{if(!id || busy || section!=='tasks')return;let active=true;const timer=setInterval(()=>{void call<Job[]>(id,'jobs').then(rows=>{if(active)setJobs(rows);}).catch(e=>{if(active)setError(String(e));});},10000);return()=>{active=false;clearInterval(timer);};},[id,busy,section]);
  const transfer=async(body:unknown)=>{const result=await call<{path:string;source?:string;files?:number}>(id,'transfer',{target:target||null,source:body});setNotice(`传输已校验完成：${result.path}${result.source?`；目标数据名称：${result.source}`:''}`);};
  return <div className="p-5 flex flex-col gap-4 max-w-[1180px] mx-auto">
    <div className="card p-4 flex flex-wrap items-center gap-3"><span>当前服务器</span><select aria-label="服务器账号" className="field !w-auto" disabled={busy} value={id} onChange={e=>setId(e.target.value)}>{hosts.map(h=><option key={h.id} value={h.id}>{h.name} · {h.username}@{h.host}</option>)}</select><button className="btn btn-ghost" disabled={busy||!id} onClick={()=>void run(refresh)}>刷新</button></div>
    {!hosts.length&&<div className="card p-5">请先在“SSH 连接”添加服务器并测试连接。</div>}
    {busy&&<div role="status">正在处理…</div>}
    {error&&<div role="alert" className="card p-4 break-words" style={{color:'var(--red)'}}>操作未完成，请查看原因后重试。<details className="mt-2 t-caption"><summary>查看原因</summary>{error}</details></div>}
    {notice&&<div role="status" className="card p-4 break-words">{notice}</div>}
    {section==='resources'&&<p className="t-caption">查看服务器有多忙，以及还剩多少可用资源。</p>}
    {section==='resources'&&metric&&<ResourceSummary value={metric}/>}
    {status&&section==='tasks'&&<div className="card p-4 flex flex-col gap-3"><div className="t-headline">{status.protection&&status.images.length&&!status.error?'分析准备已完成':'还需要准备计算环境'}</div><div className="t-caption">{status.protection&&status.images.length&&!status.error?'可以提交任务；具体分析还需对应的工具。':'点击准备环境，完成后再开始分析。'}</div><button className="btn btn-tint self-start" disabled={busy} onClick={()=>void run(async()=>{await call(id,'bootstrap',bootstrapImage.trim()?{image:bootstrapImage.trim()}:{});await refresh();setNotice('环境检查完成');})}>自动准备环境</button><details><summary className="t-caption cursor-pointer">高级设置</summary><label className="block mt-3">自定义运行镜像<input className="field w-full mt-1" value={bootstrapImage} onChange={e=>setBootstrapImage(e.target.value)} placeholder="留空使用默认环境"/></label>{status.error&&<p className="t-caption break-words mt-2">{status.error}</p>}</details></div>}
    {id&&section==='tasks'&&status&&!status.protection&&<SudoInstall id={id} label={hosts.find(h=>h.id===id)?.name||id} image={bootstrapImage.trim()||undefined}/>}
    {id&&section==='tasks'&&<>
      <div className="card p-5 flex flex-col gap-3"><div className="t-headline">安排分析任务</div><p>告诉智能体要分析什么，系统会从你选择的服务器中安排计算。这里查看进度、取消任务和下载结果。</p><button className="btn btn-primary self-start" onClick={()=>setTab('chat')}>去对话</button></div>
      <div className="card p-4"><div className="t-headline">分析任务</div>{jobs.map(job=><div className="py-3 hairline-b" key={job.id}><div>{job.spec.title||('任务 '+job.id.slice(0,8))} · {jobLabels[job.state]||'状态待确认'}</div><div className="t-caption">{job.spec.context&&<>{job.spec.context.projectName} · {job.spec.context.sessionName} · </>}{new Date(job.created*1000).toLocaleString()}</div><details className="t-caption mt-2"><summary>任务详情</summary><div className="break-all">{job.id} · {job.detail}</div></details><div className="flex flex-wrap gap-2 mt-2"><button className="btn btn-ghost" disabled={busy} onClick={()=>void run(async()=>setLog((await call<{text:string}>(id,'logs',{id:job.id})).text))}>查看日志</button>{['queued','running'].includes(job.state)?<button className="btn btn-ghost" disabled={busy} onClick={()=>void run(async()=>{await call(id,'cancel',{id:job.id});await refresh();})}>取消任务</button>:<button className="btn btn-ghost" disabled={busy} onClick={()=>void run(async()=>{const result=await call<{path:string}>(id,'transfer',{source:{job:job.id},target:null});setNotice(`结果已下载到 ${result.path}`);})}>下载结果</button>}</div></div>)}{!jobs.length&&<div className="t-caption mt-3">这台服务器还没有任务。开始分析后，进度会显示在这里。</div>}</div>
      <details className="card p-5"><summary className="cursor-pointer">高级设置：手动提交任务</summary>
      <form className="mt-4 flex flex-col gap-3" onSubmit={e=>{e.preventDefault();void run(async()=>{const parsed:unknown=JSON.parse(argv);if(!Array.isArray(parsed)||!parsed.every(a=>typeof a==='string'))throw Error('命令须为 JSON 字符串数组');const result=await call<{host:string;job:{id:string};excluded:unknown[]}>('', 'schedule',{hosts:candidates,spec:{context:await analysisContext(currentSession),id:jobId,sourceHost:id,image,argv:parsed,sources,cpu,memoryMb:memory,timeoutSeconds:timeout}});setNotice(`已提交到 ${hosts.find(h=>h.id===result.host)?.name}：${result.job.id}`);setJobId(crypto.randomUUID());if(id===result.host)await refresh();else setId(result.host);});}}>
        <div className="t-caption">熟悉命令的用户可在这里手动提交；通常在对话中说明分析需求即可。</div>
        <fieldset disabled={busy}><legend>候选服务器</legend>{hosts.map(h=><label className="inline-flex gap-2 mr-4" key={h.id}><input type="checkbox" checked={candidates.includes(h.id)} onChange={e=>setCandidates(old=>e.target.checked?[...old,h.id]:old.filter(v=>v!==h.id))}/>{h.name}</label>)}</fieldset>
        <label>允许使用的镜像<select className="field w-full" value={image} onChange={e=>setImage(e.target.value)}>{status?.images.map(v=><option key={v}>{v}</option>)}</select></label>
        <fieldset><legend>只读输入（挂载到 /input/数据名称）</legend>{Object.keys(status?.sources||{}).map(name=><label key={name} className="inline-flex gap-2 mr-4"><input type="checkbox" checked={sources.includes(name)} onChange={e=>setSources(old=>e.target.checked?[...old,name]:old.filter(v=>v!==name))}/>{name}</label>)}</fieldset>
        <label>容器内命令参数（JSON 数组，结果写入 /output）<textarea className="field w-full font-mono" rows={3} value={argv} onChange={e=>setArgv(e.target.value)}/></label>
        <div className="flex flex-wrap gap-3"><label>CPU 核数<input className="field" type="number" min={1} value={cpu} onChange={e=>setCpu(Number(e.target.value))}/></label><label>内存 MB<input className="field" type="number" min={128} value={memory} onChange={e=>setMemory(Number(e.target.value))}/></label><label>最长执行 / 排队秒数<input className="field" type="number" min={10} max={604800} value={timeout} onChange={e=>setTimeoutValue(Number(e.target.value))}/></label></div>
        <div className="t-caption">当前提交编号：{jobId}。超时后先刷新核对；重试会使用同一编号，避免重复执行。</div><button className="btn btn-primary self-start" disabled={busy||!image||!candidates.length||!argv.trim()}>自动选择服务器并提交</button>
      </form>
      </details>
      {log&&<pre className="card p-4 whitespace-pre-wrap break-all max-h-96 overflow-auto">{log}</pre>}
    </>}
    {id&&['data','protection'].includes(section)&&<><ServerWorkspaceFiles key={id} server={id}/><ServerWriteRequests key={'write-'+id} server={id}/></>}
    {id&&section==='data'&&<div className="card p-5 flex flex-col gap-3"><div className="t-headline">复制数据</div><div>把文件复制到另一台服务器或电脑，原文件保持不变。</div><div className="t-caption">复制期间请保持软件打开。每次最多 500 个文件。</div>{status&&!Object.keys(status.sources).length&&<p className="t-caption">还没有可复制的文件夹，请先到“数据保护与审计”登记数据目录。</p>}<label>选择文件夹<select className="field w-full" value={source} onChange={e=>setSource(e.target.value)}>{Object.keys(status?.sources||{}).map(name=><option key={name} value={name}>{name==='shared'?'专用共享文件夹':name}</option>)}</select></label><label>只复制其中的文件（可选）<input placeholder="留空复制整个文件夹，例如 samples/data.csv" className="field w-full" value={relative} onChange={e=>setRelative(e.target.value)}/></label><label>复制到<select className="field w-full" value={target} onChange={e=>setTarget(e.target.value)}><option value="">这台电脑</option>{hosts.filter(h=>h.id!==id).map(h=><option value={h.id} key={h.id}>{h.name} · {h.username}</option>)}</select></label><button className="btn btn-primary self-start" disabled={busy||!source} onClick={()=>void run(()=>transfer({source,path:relative.trim()||'.'}))}>开始复制</button></div>}
    {status&&section==='protection'&&<>
      <div className="card p-5 flex flex-col gap-3"><div className="t-headline">保护原始数据</div><p>所有一体化分析流程和服务器工具分析都只读原始文件；配置、索引、结果写入 OMaaX 工作文件夹，并记录操作。</p><div className="t-caption">可读取当前 SSH 账号有权限访问的数据路径，无需逐个登记。可以在账号有权限的任意目录保存新文件；覆盖已有文件需你明确授权，删除仅限工作文件夹。此功能不包含自动备份。</div>{!status.protection&&<div>分析保护环境尚未就绪，请到“任务与调度”准备环境。</div>}<details><summary className="cursor-pointer">管理保护设置</summary>
      <form className="mt-4 flex flex-col gap-3" onSubmit={e=>{e.preventDefault();void run(async()=>{const roots:Record<string,string>={};for(const line of sourceText.split('\n').filter(l=>l.trim())){const n=line.indexOf('=');if(n<1)throw Error('目录格式应为 数据名称=/绝对路径');const name=line.slice(0,n).trim();if(name in roots)throw Error('数据名称重复');roots[name]=line.slice(n+1).trim();}await call(id,'configure',{sources:roots,images:imageText.split('\n').map(l=>l.trim()).filter(Boolean)});await refresh();setNotice('只读输入和镜像范围已保存');});}}><label>数据目录（每行：数据名称=/绝对路径）<textarea rows={5} className="field w-full font-mono" value={sourceText} onChange={e=>setSourceText(e.target.value)}/></label><label>允许的镜像摘要（每行 image@sha256:… 或离线镜像 sha256:…）<textarea rows={3} className="field w-full font-mono" value={imageText} onChange={e=>setImageText(e.target.value)}/></label><button className="btn btn-primary self-start" disabled={busy}>保存保护范围</button><div className="t-caption">执行记录与输出目录：{status.root}。数据副本可在“跨服务器数据”中导出保存；不会自动清理任务或副本。</div></form>
      </details></div>
      <div className="card p-4"><div className="t-headline">操作记录</div><p className="t-caption mt-2">查看任务执行和数据传输记录，方便排查问题。</p>{!audit.length&&<p className="t-caption mt-3">还没有操作记录。</p>}{audit.map(row=><div className="py-2 hairline-b break-words" key={row.id}><div>{new Date(row.at*1000).toLocaleString()} · {auditLabels[row.action]||(row.action.startsWith('job.')?`任务${jobLabels[row.action.slice(4)]||'状态已更新'}`:'操作已记录')}</div><details className="t-caption mt-1"><summary>查看详情</summary><div>{row.detail}</div></details></div>)}</div>
    </>}
  </div>;
}
