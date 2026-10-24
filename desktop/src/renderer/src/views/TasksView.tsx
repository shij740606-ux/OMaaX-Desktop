import {serverDisplayMessage} from '../serverDisplay';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, FileOutput, Loader2, Play, Square } from 'lucide-react';
import type { PlatformJob, PlatformJobArtifact, PlatformJobEvent, PlatformJobStatus, SchedulingNode, SchedulingDecision } from '@shared/types';
import { useApp } from '../store';
import { useT } from '../i18n';
import ExecutionProtection from '../components/ExecutionProtection';
import { storageCall } from '../components/RemoteData';

const active = new Set<PlatformJobStatus>(['queued', 'assigned', 'preparing', 'running', 'cancelling']);
const colors: Record<PlatformJobStatus, string> = {
  queued: 'var(--orange)', assigned: 'var(--accent)', preparing: 'var(--accent)', running: 'var(--green)',
  cancelling: 'var(--orange)', cancelled: 'var(--text-3)', succeeded: 'var(--green)', failed: 'var(--red)', unknown: 'var(--red)',
};
const bytes = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MB` : `${value} B`;

export default function TasksView() {
  const { platform, settings } = useApp();
  const {t,lang}=useT();
  const projectId = settings?.currentProjectId ?? '';
  const role = platform.projects.find((project) => project.id === projectId)?.role;
  const canSubmit = platform.user?.isAdmin || role === 'owner' || role === 'analyst';
  const [nodes, setNodes] = useState<SchedulingNode[]>([]);
  const [nodeIds, setNodeIds] = useState<string[]>([]);
  const [restricted, setRestricted] = useState(false);
  const [gpuMemory, setGpuMemory] = useState('0');
  const [decision, setDecision] = useState<SchedulingDecision | null>(null);
  useEffect(() => { void window.biodsh.platformSchedulingNodes().then(setNodes).catch(reason => setError(String(reason).replace(/^Error:\s*/,''))); }, [projectId]);
  const [jobs, setJobs] = useState<PlatformJob[]>([]);
  const [kinds, setKinds] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [events, setEvents] = useState<PlatformJobEvent[]>([]);
  const [artifacts, setArtifacts] = useState<PlatformJobArtifact[]>([]);
  const [kind, setKind] = useState('');
  const [argumentsText, setArgumentsText] = useState('');
  const [cpu, setCpu] = useState('1');
  const [memoryGb, setMemoryGb] = useState('1');
  const [gpu, setGpu] = useState('0');
  const [busy, setBusy] = useState(false);
  const [dataVersions, setDataVersions] = useState<{id:string;name:string;manifest_sha256:string}[]>([]);
  const [dataIds, setDataIds] = useState<string[]>([]);
  const [grantNotice, setGrantNotice] = useState('');
  useEffect(() => { let live=true; setDataIds([]); setDataVersions([]); setGrantNotice('');
    if(projectId) void storageCall('versions',{project_id:projectId}).then(value => {if(live)setDataVersions(value as typeof dataVersions);}).catch(reason => live && setError(String(reason).replace(/^Error:\s*/,'')));
    return () => {live=false;};
  },[projectId]);
  const [error, setError] = useState('');
  const selected = useMemo(() => jobs.find((job) => job.id === selectedId), [jobs, selectedId]);

  const load = useCallback(async () => {
    if (!projectId) { setJobs([]); return; }
    try {
      const [next, nextKinds] = await Promise.all([window.biodsh.platformJobs(projectId), window.biodsh.platformJobKinds()]);
      setJobs(next);
      setKinds(nextKinds);
      const id = next.some((job) => job.id === selectedId) ? selectedId : next[0]?.id ?? '';
      setSelectedId(id);
      if (id) {
        const [nextEvents, nextArtifacts] = await Promise.all([window.biodsh.platformJobEvents(id), window.biodsh.platformJobArtifacts(id)]);
        setEvents(nextEvents); setArtifacts(nextArtifacts);
        setDecision(await window.biodsh.platformScheduling(id));
      } else { setEvents([]); setArtifacts([]); }
      setError('');
    } catch (reason) { setError(String(reason).slice(0, 160)); }
  }, [projectId, selectedId]);

  useEffect(() => { void load(); const timer = setInterval(() => void load(), 2_000); return () => clearInterval(timer); }, [load]);

  const submit = async () => {
    if (!projectId || !kind.trim() || busy) return;
    setBusy(true);
    try {
      const created = await window.biodsh.platformCreateJob({
        dataVersionIds: dataIds,
        nodeIds: restricted ? nodeIds : undefined,
        projectId, kind: kind.trim(), arguments: argumentsText.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        requestedResources: { cpu: Math.ceil(Math.max(0, Number(cpu) || 0)), memory_bytes: Math.round(Math.max(0, Number(memoryGb) || 0) * 1024 ** 3), gpu_memory_mib: Math.round(Math.max(0, Number(gpuMemory) || 0) * 1024), gpu_count: Math.ceil(Math.max(0, Number(gpu) || 0)) },
      });
      setSelectedId(created.id); setArgumentsText(''); await load();
    } catch (reason) { setError(String(reason).slice(0, 160)); }
    finally { setBusy(false); }
  };

  const cancel = async () => {
    if (!selected || busy) return;
    setBusy(true);
    try { await window.biodsh.platformCancelJob(selected.id); await load(); }
    catch (reason) { setError(String(reason).slice(0, 160)); }
    finally { setBusy(false); }
  };

  const download = async (artifact: PlatformJobArtifact) => {
    if (!selected || busy) return;
    setBusy(true);
    try {
      const path = await window.biodsh.platformDownloadJobArtifact(selected.id, artifact);
      await window.biodsh.openPath(path);
    } catch (reason) { setError(String(reason).slice(0, 160)); }
    finally { setBusy(false); }
  };

  if (!projectId) return <div className="p-6"><div className="card p-6">{t('请先选择一个项目。')}</div></div>;
  return <div className="h-full overflow-y-auto p-6"><div className="max-w-[1180px] mx-auto flex flex-col gap-4">
    <div><div className="t-title">{t('计算任务')}</div><div className="t-caption mt-1">{t('提交重计算任务，并实时查看服务器日志和结果。')}</div></div>
    {canSubmit && <div className="card p-4 grid lg:grid-cols-[1fr_1.4fr_auto_auto_auto_auto] gap-3 items-end">
      <label className="t-caption">{t('任务类型')}<input className="input mt-1 w-full" list="biodsh-job-kinds" value={kind} onChange={(e) => setKind(e.target.value)} placeholder="scrna-qc" /><datalist id="biodsh-job-kinds">{kinds.map((value) => <option key={value} value={value} />)}</datalist></label>
      <div className="t-caption">{t("只读数据版本")}{dataVersions.map(v=><label className="block" key={v.id}><input type="checkbox" checked={dataIds.includes(v.id)} onChange={e=>setDataIds(previous=>e.target.checked?[...previous,v.id]:previous.filter(id=>id!==v.id))}/> {v.name} · {v.manifest_sha256.slice(0,10)}</label>)}{!dataVersions.length && <div>{t("暂无已登记版本，请管理员登记后选择。")}</div>}</div>
      <button className="btn btn-ghost" disabled={!kind.trim() || busy || !dataIds.length} onClick={()=>void (async()=>{setBusy(true);try{await storageCall('agent-enable',{project_id:projectId,grant:{kinds:[kind.trim()],data_version_ids:dataIds,cpu:Math.max(1,Number(cpu)||1),memory_bytes:Math.max(67108864,Math.round((Number(memoryGb)||0.25)*1024**3))}});setGrantNotice('已授权所选数据和任务类型，有效 15 分钟。首次使用或切换项目后请重启智能体。');}catch(reason){setError(String(reason).replace(/^Error:\s*/,''));}finally{setBusy(false);}})()}>{t("授权本次对话")}</button>
      {grantNotice && <div role="status" className="t-caption">{t(grantNotice)}</div>}
      <label className="t-caption">{t('参数（每行一个）')}<textarea className="input mt-1 w-full min-h-[42px]" value={argumentsText} onChange={(e) => setArgumentsText(e.target.value)} /></label>
      <label className="t-caption">CPU<input className="input mt-1 w-20" type="number" min="0" value={cpu} onChange={(e) => setCpu(e.target.value)} /></label>
      <label className="t-caption">{t('内存 GB')}<input className="input mt-1 w-20" type="number" min="0" step="0.5" value={memoryGb} onChange={(e) => setMemoryGb(e.target.value)} /></label>
      <label className="t-caption">GPU<input className="input mt-1 w-20" type="number" min="0" value={gpu} onChange={(e) => setGpu(e.target.value)} /></label>
      <button className="btn btn-primary" disabled={!kind.trim() || busy || (restricted && !nodeIds.length)} onClick={() => void submit()}>{busy ? <Loader2 size={15} className="spin" /> : <Play size={15} />}{t('提交')}</button>
    </div>}
    {canSubmit && <div className="card p-4 flex flex-col gap-3"><label><input type="checkbox" checked={restricted} onChange={event => setRestricted(event.target.checked)} /> {t("限定服务器范围（不勾选则自动选择全部计算节点；选择一台即可固定服务器）")}</label>
     {restricted && <><div className="flex flex-wrap gap-3">{nodes.map(node => <label key={node.id}><input type="checkbox" checked={nodeIds.includes(node.id)} onChange={event => setNodeIds(previous => event.target.checked ? [...previous,node.id] : previous.filter(id => id !== node.id))} /> {node.name} · {node.status}</label>)}</div><button className="btn btn-ghost self-start" onClick={() => void window.biodsh.sshHosts().then(hosts => {setNodeIds([...new Set(hosts.filter(h => h.selected && h.workerNodeId).map(h => h.workerNodeId!))]);setError(hosts.some(h => h.selected && !h.workerNodeId) ? "部分所选 SSH 服务器尚未绑定 Worker，不参与调度" : '');})}>{t("使用服务器管理中勾选的计算节点")}</button></>}
     <label>{t("每张 GPU 最小显存 GiB")}<input className="input ml-2 w-20" type="number" min={0} value={gpuMemory} onChange={event => setGpuMemory(event.target.value)} /></label><div className="t-caption">{t("每个任务选择一台服务器；资源不足时排队。")}{restricted && !nodeIds.length && t("请至少选择一台节点。")}</div></div>}
    {error && <div className="card p-3 t-caption" style={{ color: 'var(--red)' }}>{serverDisplayMessage(t,error || '')}</div>}
    <div className="grid lg:grid-cols-[330px_1fr] gap-4 min-h-[520px]">
      <div className="card p-2 overflow-y-auto">{jobs.length === 0 ? <div className="p-6 text-center t-caption">{t('暂无计算任务')}</div> : jobs.map((job) => <button key={job.id} className={`w-full text-left p-3 rounded-xl mb-1 ${selectedId === job.id ? 'bg-[var(--accent-soft)]' : ''}`} onClick={() => setSelectedId(job.id)}><div className="flex items-center gap-2"><span className="w-2 h-2 rounded-full" style={{ background: colors[job.status] }} /><span className="font-medium flex-1 truncate">{job.kind}</span><span className="t-caption">{t(job.status)}</span></div><div className="t-caption mt-1">{new Date(job.createdAt).toLocaleString(lang==='en'?'en-US':'zh-CN')} · #{job.id.slice(0, 8)}</div></button>)}</div>
      <div className="flex flex-col gap-3">{!selected ? <div className="card p-8 text-center t-caption">{t('选择一个任务查看详情')}</div> : <>
        <div className="card p-4 flex items-center gap-3"><div className="flex-1"><div className="t-headline">{selected.kind}</div><div className="t-caption">{t('状态')}：<span style={{ color: colors[selected.status] }}>{t(selected.status)}</span> · {t('尝试次数')}：{selected.attempt}{selected.nodeId ? ` · Node ${selected.nodeId.slice(0, 8)}` : ''}</div></div>{canSubmit && active.has(selected.status) && <button className="btn btn-ghost" disabled={busy || selected.status === 'cancelling'} onClick={() => void cancel()}><Square size={14} />{t('取消任务')}</button>}</div>
        <ExecutionProtection job={selected} />
        {selected.status === 'queued' && decision && <div className="card p-4"><div className="t-headline">{t("调度状态")}</div><div className="t-caption mt-2">{serverDisplayMessage(t,decision.reason)}</div>{decision.nodes.map(node => <div key={node.node_id} className="t-caption mt-2">{node.name}：{node.eligible ? t("满足需求，等待 Worker 领取") : serverDisplayMessage(t,node.reason)} {t("· 空闲 CPU")}{node.cpu_free} {t("· 可用内存")}{bytes(node.memory_free)}</div>)}</div>}
        <div className="card p-4"><div className="t-headline mb-3">{t('实时日志')}</div><pre className="text-xs whitespace-pre-wrap overflow-auto min-h-[250px] max-h-[360px] p-3 rounded-lg" style={{ background: 'var(--bg)' }}>{events.map((event) => `[${new Date(event.createdAt).toLocaleTimeString(lang==='en'?'en-US':'zh-CN')}] ${event.message}`).join('\n') || t('等待服务器输出…')}</pre></div>
        <div className="card p-4"><div className="t-headline mb-3 flex items-center gap-2"><FileOutput size={16} />{t('结果文件')}</div>{artifacts.length === 0 ? <div className="t-caption">{t('任务完成后，结果文件会显示在这里。')}</div> : artifacts.map((artifact) => <div key={artifact.id} className="flex items-center gap-3 py-2 border-b border-[var(--border)] last:border-0"><span className="flex-1 truncate">{artifact.relativePath}</span><span className="t-caption">{bytes(artifact.sizeBytes)}</span><span className="t-caption font-mono">{artifact.sha256.slice(0, 10)}</span><button className="btn btn-ghost" disabled={busy} onClick={() => void download(artifact)}><Download size={13} />{t('下载')}</button></div>)}</div>
        {selected.errorMessage && <div className="card p-3 t-caption" style={{ color: 'var(--red)' }}>{t(selected.errorMessage)}</div>}
      </>}</div>
    </div>
  </div></div>;
}
