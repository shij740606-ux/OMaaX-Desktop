import {serverError} from '../serverErrors';
import { useEffect, useRef, useState } from 'react';
import type { SshHost, SshMetrics } from '@shared/types';
import { ResourceSummary } from './HostsView';
import ServerWriteRequests from '../components/ServerWriteRequests';
import ServerDeleteRequests from '../components/ServerDeleteRequests';
import ServerTransfers from '../components/ServerTransfers';
import ServerWorkspaceFiles from '../components/ServerWorkspaceFiles';
import ServerEnvironments from '../components/ServerEnvironments';
import ServerFileBrowser from '../components/ServerFileBrowser';
import {analysisContext,type AnalysisContext} from './analysisContext';
import { useApp } from '../store';
import {invalidateServer} from '../serverRequests';
import {connectionRevision} from '../serverConnections';
const jobLabels:Record<string,string>={queued:'等待资源',running:'分析中',succeeded:'已完成',complete:'已完成',partial:'部分完成',invalid_result:'结果不满足要求',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
const auditLabels:Record<string,string>={'bootstrap.shared_ready':'共享文件夹已准备','bootstrap.install_packages':'安装计算环境','bootstrap.image_ready':'运行环境已准备','bootstrap.ready':'环境检查完成',configure:'更新保护设置','job.cancel_requested':'请求取消任务','data.export':'导出数据','data.import_pending':'开始接收数据','data.import_complete':'数据接收完成'};

type Status = {uid:number; root:string; protection:unknown; error:string; sources:Record<string,string>; images:string[]};
type Job = {id:string; state:string; detail:string; created:number; spec:{argv:string[]; sources:string[];title?:string;context?:AnalysisContext}};
type Audit = {id:number; at:number; action:string; detail:string};
const savedResults=new Map<string,{status?:Status;metric?:SshMetrics;jobs?:Job[];audit?:Audit[]}>();
const call = <T,>(id:string,action:string,body:unknown={}) => window.biodsh.personalCompute(id,action,body) as Promise<T>;

export default function PersonalComputeView({section,onManageEnvironment,resultsOnly=false}:{resultsOnly?:boolean;section:'resources'|'tasks'|'data'|'protection'|'environments'|'files';onManageEnvironment?:()=>void}) {
  const {setTab,currentSession}=useApp();
  const [hosts,setHosts]=useState<SshHost[]>([]), [id,setId]=useState(()=>sessionStorage.getItem('omaax.server.selected')||'');
  const currentId=useRef(id);currentId.current=id;const generation=useRef(0);
  useEffect(()=>{if(id)sessionStorage.setItem('omaax.server.selected',id);},[id]);
  const [status,setStatus]=useState<Status|null>(null), [metric,setMetric]=useState<SshMetrics|null>(null);
  const [jobs,setJobs]=useState<Job[]>([]), [audit,setAudit]=useState<Audit[]>([]);
  const [busy,setBusy]=useState(false), [error,setError]=useState(''), [notice,setNotice]=useState('');
  const [log,setLog]=useState(''), [sourceText,setSourceText]=useState(''), [imageText,setImageText]=useState('');
  const [source,setSource]=useState(''), [relative,setRelative]=useState(''), [target,setTarget]=useState('');
  const [environmentRefresh,setEnvironmentRefresh]=useState(0);
  const run=async(work:()=>Promise<void>)=>{setBusy(true);setError('');setNotice('');try{await work();}catch(e){setError(serverError(e));}finally{setBusy(false);}};
  useEffect(()=>{void window.biodsh.sshHosts().then(rows=>{const trusted=rows.filter(h=>h.hostKey);setHosts(trusted);setId(trusted.find(h=>{try{return h.id===JSON.parse(sessionStorage.getItem('omaax.files.open')||'{}').serverId;}catch{return false;}})?.id||trusted.find(h=>h.id===sessionStorage.getItem('omaax.server.selected'))?.id||trusted[0]?.id||'');}).catch(e=>setError(serverError(e)));},[]);
  const refresh=async()=>{
    if(!id||section==='environments'||section==='files')return;const revision=connectionRevision(id),cacheKey=id+'@'+revision,expected=++generation.current;const valid=()=>currentId.current===id&&generation.current===expected&&connectionRevision(id)===revision;
    if(section==='resources'){const next=await window.biodsh.sshHostMetrics(id);if(valid()){setMetric(next);savedResults.set(cacheKey,{...savedResults.get(cacheKey),metric:next});}return;}
    const next=await call<Status>(id,'status');if(!valid())return;setStatus(next);savedResults.set(cacheKey,{...savedResults.get(cacheKey),status:next});
    if(section==='tasks'){const rows=await call<Job[]>(id,'jobs');if(valid()){setJobs(rows);savedResults.set(cacheKey,{...savedResults.get(cacheKey),jobs:rows});}}
    if(section==='protection'){const rows=await call<Audit[]>(id,'audit');if(valid()){setAudit(rows);savedResults.set(cacheKey,{...savedResults.get(cacheKey),audit:rows});}}
  };
  useEffect(()=>{const saved=savedResults.get(id+'@'+connectionRevision(id));setStatus(saved?.status||null);setMetric(saved?.metric||null);setJobs(saved?.jobs||[]);setAudit(saved?.audit||[]);setLog('');setSource('');let active=true;setBusy(true);setError('');void refresh().catch(e=>{if(active)setError(serverError(e));}).finally(()=>{if(active)setBusy(false);});return()=>{active=false;generation.current++;};},[id,section]);
  useEffect(()=>{const finished=(event:Event)=>{if((event as CustomEvent<{id:string}>).detail.id===id)void refresh().catch(e=>setError(serverError(e)));};window.addEventListener('biodsh-install-finished',finished);return()=>window.removeEventListener('biodsh-install-finished',finished);},[id,section]);
  useEffect(()=>{if(!status)return;setSourceText(Object.entries(status.sources).map(([k,v])=>`${k}=${v}`).join('\n'));setImageText(status.images.join('\n'));setSource(old=>old in status.sources?old:Object.keys(status.sources)[0]||'');},[status]);
  useEffect(()=>{if(!id || busy || section!=='tasks')return;let active=true;const timer=setInterval(()=>{void call<Job[]>(id,'jobs').then(rows=>{if(active)setJobs(rows);}).catch(e=>{if(active)setError(serverError(e));});},10000);return()=>{active=false;clearInterval(timer);};},[id,busy,section]);
  const transfer=async(body:unknown)=>{const result=await call<{path:string;source?:string;files?:number}>(id,'transfer',{target:target||null,source:{...(body as object),context:await analysisContext(currentSession)}});setNotice(`传输已校验完成：${result.path}${result.source?`；目标数据名称：${result.source}`:''}`);};
  return <div className="p-5 flex flex-col gap-4 max-w-[1180px] mx-auto">
    <div className="card p-4 flex flex-wrap items-center gap-3"><span>当前服务器</span><select aria-label="服务器账号" className="field !w-auto" disabled={busy} value={id} onChange={e=>setId(e.target.value)}>{hosts.map(h=><option key={h.id} value={h.id}>{h.name} · {h.username}@{h.host}</option>)}</select><button className="btn btn-ghost" disabled={busy||!id} onClick={()=>{invalidateServer(id);if(section==='environments'||section==='files')setEnvironmentRefresh(n=>n+1);else void run(refresh);}}>刷新</button></div>
    {!hosts.length&&<div className="card p-5">请先在“SSH 连接”添加服务器并测试连接。</div>}
    {busy&&<div role="status">正在处理…</div>}
    {error&&<div role="alert" className="card p-4 break-words" style={{color:'var(--red)'}}>操作未完成，请查看原因后重试。<details className="mt-2 t-caption"><summary>查看原因</summary>{error}</details></div>}
    {notice&&<div role="status" className="card p-4 break-words">{notice}</div>}
    {section==='resources'&&<p className="t-caption">查看服务器有多忙，以及还剩多少可用资源。</p>}
    {section==='resources'&&metric&&<ResourceSummary value={metric}/>}
    {!resultsOnly&&status&&section==='tasks'&&(!status.protection||status.error)&&<div className="card p-4"><p>服务器环境尚未就绪，请先到“环境管理”检查和配置。</p>{onManageEnvironment&&<button className="btn btn-tint mt-2" onClick={onManageEnvironment}>去环境管理</button>}</div>}
    {id&&section==='tasks'&&<>
      {!resultsOnly&&<div className="card p-5 flex flex-col gap-3"><div className="t-headline">安排分析任务</div><p>告诉智能体要分析什么，系统会从你选择的服务器中安排计算。这里查看进度、取消任务和下载结果。</p><button className="btn btn-primary self-start" onClick={()=>setTab('chat')}>去对话</button></div>}
      <div className="card p-4"><div className="t-headline">分析任务</div>{jobs.map(job=><div className="py-3 hairline-b" key={job.id}><div>{job.spec.title||('任务 '+job.id.slice(0,8))} · {jobLabels[job.state]||'状态待确认'}</div><div className="t-caption">{job.spec.context&&<>{job.spec.context.projectName} · {job.spec.context.sessionName} · </>}{new Date(job.created*1000).toLocaleString()}</div><details className="t-caption mt-2"><summary>任务详情</summary><div className="break-all">{job.id} · {job.detail?serverError(job.detail):''}</div></details><div className="flex flex-wrap gap-2 mt-2"><button className="btn btn-ghost" disabled={busy} onClick={()=>void run(async()=>setLog((await call<{text:string}>(id,'logs',{id:job.id})).text))}>查看日志</button>{['queued','running'].includes(job.state)?<button className="btn btn-ghost" disabled={busy} onClick={()=>void run(async()=>{await call(id,'cancel',{id:job.id});await refresh();})}>取消任务</button>:<button className="btn btn-ghost" disabled={busy} onClick={()=>void run(async()=>{const result=await call<{path:string}>(id,'transfer',{source:{job:job.id},target:null});setNotice(`结果已下载到 ${result.path}`);})}>下载结果</button>}</div></div>)}{!jobs.length&&<div className="t-caption mt-3">这台服务器还没有任务。开始分析后，进度会显示在这里。</div>}</div>
      {log&&<pre className="card p-4 whitespace-pre-wrap break-all max-h-96 overflow-auto">{log}</pre>}
    </>}
    {id&&resultsOnly&&<ServerWorkspaceFiles key={id} server={id}/>}
    {id&&section==='protection'&&<><ServerDeleteRequests server={id}/><ServerWorkspaceFiles key={id} server={id}/><ServerWriteRequests key={'write-'+id} server={id}/></>}
    {id&&section==='environments'&&<ServerEnvironments key={id} server={id} label={hosts.find(h=>h.id===id)?.name||id} refreshToken={environmentRefresh}/>}
    {id&&section==='files'&&<ServerFileBrowser key={id} server={id} refreshToken={environmentRefresh}/>}
    {id&&section==='data'&&<ServerTransfers server={id}/>}
    {id&&section==='data'&&<div className="card p-5 flex flex-col gap-3"><div className="t-headline">复制数据</div><div>把文件复制到另一台服务器或电脑，原文件保持不变。</div><div className="t-caption">复制期间请保持软件打开。每次最多 500 个文件。</div>{status&&!Object.keys(status.sources).length&&<p className="t-caption">还没有可复制的文件夹，请先到“数据保护与审计”登记数据目录。</p>}<label>选择文件夹<select className="field w-full" value={source} onChange={e=>setSource(e.target.value)}>{Object.keys(status?.sources||{}).map(name=><option key={name} value={name}>{name==='shared'?'专用共享文件夹':name}</option>)}</select></label><label>只复制其中的文件（可选）<input placeholder="留空复制整个文件夹，例如 samples/data.csv" className="field w-full" value={relative} onChange={e=>setRelative(e.target.value)}/></label><label>复制到<select className="field w-full" value={target} onChange={e=>setTarget(e.target.value)}><option value="">这台电脑</option>{hosts.filter(h=>h.id!==id).map(h=><option value={h.id} key={h.id}>{h.name} · {h.username}</option>)}</select></label><button className="btn btn-primary self-start" disabled={busy||!source} onClick={()=>void run(()=>transfer({source,path:relative.trim()||'.'}))}>开始复制</button></div>}
    {status&&section==='protection'&&<>
      <div className="card p-5 flex flex-col gap-3"><div className="t-headline">保护原始数据</div><p>所有一体化分析流程和服务器工具分析都只读原始文件；配置、索引、结果写入 OMaaX 工作文件夹，并记录操作。</p><div className="t-caption">可读取当前 SSH 账号有权限访问的数据路径，无需逐个登记。可以在账号有权限的任意目录保存新文件；覆盖已有文件需你明确授权，账号有权限的位置可按要求删除；通配符、递归或超过 10 GB 会先展示删除提醒。此功能不包含自动备份。</div>{!status.protection&&<div>分析保护环境尚未就绪，请到“环境管理”准备环境。</div>}<details><summary className="cursor-pointer">管理保护设置</summary>
      <form className="mt-4 flex flex-col gap-3" onSubmit={e=>{e.preventDefault();void run(async()=>{const roots:Record<string,string>={};for(const line of sourceText.split('\n').filter(l=>l.trim())){const n=line.indexOf('=');if(n<1)throw Error('目录格式应为 数据名称=/绝对路径');const name=line.slice(0,n).trim();if(name in roots)throw Error('数据名称重复');roots[name]=line.slice(n+1).trim();}await call(id,'configure',{sources:roots,images:imageText.split('\n').map(l=>l.trim()).filter(Boolean)});await refresh();setNotice('只读输入和镜像范围已保存');});}}><label>数据目录（每行：数据名称=/绝对路径）<textarea rows={5} className="field w-full font-mono" value={sourceText} onChange={e=>setSourceText(e.target.value)}/></label><label>允许的镜像摘要（每行 image@sha256:… 或离线镜像 sha256:…）<textarea rows={3} className="field w-full font-mono" value={imageText} onChange={e=>setImageText(e.target.value)}/></label><button className="btn btn-primary self-start" disabled={busy}>保存保护范围</button><div className="t-caption">执行记录与输出目录：{status.root}。数据副本可在“跨服务器数据”中导出保存；不会自动清理任务或副本。</div></form>
      </details></div>
      <div className="card p-4"><div className="t-headline">操作记录</div><p className="t-caption mt-2">查看任务执行和数据传输记录，方便排查问题。</p>{!audit.length&&<p className="t-caption mt-3">还没有操作记录。</p>}{audit.map(row=><div className="py-2 hairline-b break-words" key={row.id}><div>{new Date(row.at*1000).toLocaleString()} · {auditLabels[row.action]||(row.action.startsWith('job.')?`任务${jobLabels[row.action.slice(4)]||'状态已更新'}`:'操作已记录')}</div><details className="t-caption mt-1"><summary>查看详情</summary><div>{row.detail}</div></details></div>)}</div>
    </>}
  </div>;
}
