import {serverDisplayMessage} from '../serverDisplay';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Cpu, Database, HardDrive, Loader2, MemoryStick, Server } from 'lucide-react';
import type { PlatformNode, PlatformNodeControlStatus, PlatformNodeMetric, PlatformNodeStatus } from '@shared/types';
import { useApp } from '../store';
import { useT } from '../i18n';

const statusText: Record<PlatformNodeStatus, string> = {
  online: '在线', offline: '离线', draining: '排空中', disabled: '已禁用',
};
const statusColor: Record<PlatformNodeStatus, string> = {
  online: 'var(--green)', offline: 'var(--text-3)', draining: 'var(--orange)', disabled: 'var(--red)',
};
const bytes = (value: number) => {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let amount = value; let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(unit > 2 ? 1 : 0)} ${units[unit]}`;
};

function Sparkline({ values, color }: { values: number[]; color: string }) {
 const {t,lang}=useT();
  if (values.length < 2) return <div className="h-[72px]" />;
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 100},${68 - Math.min(100, Math.max(0, value)) * 0.64}`).join(' ');
  return <svg className="w-full h-[72px]" viewBox="0 0 100 72" preserveAspectRatio="none" aria-hidden="true"><line x1="0" y1="68" x2="100" y2="68" stroke="var(--border)" /><polyline points={points} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg>;
}

function MetricCard({ icon: Icon, label, value, detail }: { icon: typeof Cpu; label: string; value: string; detail?: string }) {
 const {t,lang}=useT();
  return <div className="card p-4 flex flex-col gap-1"><div className="flex items-center gap-2 t-caption"><Icon size={14} />{t(label || '')}</div><div className="text-[22px] font-semibold">{value}</div>{detail && <div className="t-caption">{serverDisplayMessage(t,detail || '')}</div>}</div>;
}

export default function ServersView() {
  const { platform } = useApp();
  const {t,lang}=useT();
  const [nodes, setNodes] = useState<PlatformNode[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [history, setHistory] = useState<PlatformNodeMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const selected = useMemo(() => nodes.find((node) => node.id === selectedId), [nodes, selectedId]);

  const load = useCallback(async () => {
    if (!platform.user?.isAdmin) { setLoading(false); return; }
    try {
      const next = await window.biodsh.platformNodes();
      setNodes(next);
      const id = next.some((node) => node.id === selectedId) ? selectedId : next[0]?.id ?? '';
      setSelectedId(id);
      setHistory(id ? await window.biodsh.platformNodeMetrics(id, 60) : []);
      setError('');
    } catch (reason) {
      setError(t('刷新服务器失败：{msg}', { msg: String(reason).slice(0, 120) }));
    } finally { setLoading(false); }
  }, [platform.user?.isAdmin, selectedId]);

  useEffect(() => { void load(); const timer = setInterval(() => void load(), 10_000); return () => clearInterval(timer); }, [load]);
  const changeStatus = async (status: PlatformNodeControlStatus) => {
    if (!selected) return;
    try {
      await window.biodsh.platformUpdateNode(selected.id, status);
      await load();
    } catch (reason) {
      setError(t('服务器状态更新失败：{msg}', { msg: String(reason).slice(0, 120) }));
    }
  };
  const metric = selected?.latestMetric;

  return <div className="h-full overflow-y-auto p-6">
    <div className="max-w-[1180px] mx-auto flex flex-col gap-5">

      <div><div className="t-title">{t('服务器资源')}</div><div className="t-caption mt-1">{t('统一查看一台或多台计算服务器的在线状态和资源使用情况。')}</div></div>
      {!platform.user?.isAdmin && <div className="card p-5">{t('只有系统管理员可以查看和控制计算服务器。')}</div>}
      {platform.user?.isAdmin && loading && <div className="card p-5 flex items-center gap-2"><Loader2 size={16} className="spin" />{t('刷新')}</div>}
      {platform.user?.isAdmin && !loading && nodes.length === 0 && <div className="card p-8 text-center"><Server size={28} className="mx-auto mb-3" /><div className="t-headline">{t('还没有服务器')}</div><div className="t-caption mt-1">{t('在服务器安装并注册 OMaaX Worker 后，它会自动出现在这里。')}</div></div>}
      {error && <div className="card p-3 t-caption" style={{ color: 'var(--red)' }}>{serverDisplayMessage(t,error || '')}</div>}
      {nodes.length > 0 && <>
        <div className="flex flex-wrap gap-2">{nodes.map((node) => <button key={node.id} className={`btn ${node.id === selectedId ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setSelectedId(node.id)}><span className="w-2 h-2 rounded-full" style={{ background: statusColor[node.status] }} />{node.name}</button>)}</div>
        {selected && <>
          <div className="card p-5 flex flex-wrap items-center gap-4"><div className="w-11 h-11 rounded-xl flex items-center justify-center" style={{ background: 'var(--accent-soft)' }}><Server size={21} /></div><div className="flex-1 min-w-[220px]"><div className="t-headline flex items-center gap-2">{selected.name}<span className="badge" style={{ color: statusColor[selected.status] }}>{t(statusText[selected.status])}</span></div><div className="t-caption">{selected.hostname} · {selected.operatingSystem}</div><div className="t-caption">{t('最后心跳')}：{selected.lastHeartbeatAt ? new Date(selected.lastHeartbeatAt).toLocaleString(lang==='en'?'en-US':'zh-CN') : '—'} · Worker {selected.agentVersion || '—'}</div></div><div className="flex gap-2">{selected.status !== 'online' && <button className="btn btn-ghost" onClick={() => void changeStatus('online')}>{t('启用服务器')}</button>}<button className="btn btn-ghost" onClick={() => void changeStatus('draining')}>{t('暂停接收任务')}</button><button className="btn btn-ghost" onClick={() => void changeStatus('disabled')}>{t('禁用服务器')}</button></div></div>
          {!metric ? <div className="card p-5">{t('资源数据尚未上报')}</div> : <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3"><MetricCard icon={Cpu} label="CPU" value={`${metric.cpuPercent.toFixed(1)}%`} detail={metric.loadOne == null ? undefined : `Load ${metric.loadOne.toFixed(2)}`} /><MetricCard icon={MemoryStick} label={t('内存')} value={`${metric.memoryPercent.toFixed(1)}%`} detail={`${bytes(metric.memoryUsedBytes)} / ${bytes(metric.memoryTotalBytes)}`} /><MetricCard icon={HardDrive} label={t('磁盘')} value={`${metric.diskPercent.toFixed(1)}%`} detail={`${bytes(metric.diskUsedBytes)} / ${bytes(metric.diskTotalBytes)}`} /><MetricCard icon={Database} label={t('运行任务')} value={String(metric.runningJobs)} /></div>
            <div className="grid lg:grid-cols-3 gap-3"><div className="card p-4"><div className="t-caption">CPU · {t('最近一小时')}</div><Sparkline values={history.map((row) => row.cpuPercent)} color="var(--accent)" /></div><div className="card p-4"><div className="t-caption">{t('内存')} · {t('最近一小时')}</div><Sparkline values={history.map((row) => row.memoryPercent)} color="var(--green)" /></div><div className="card p-4"><div className="t-caption">{t('磁盘')} · {t('最近一小时')}</div><Sparkline values={history.map((row) => row.diskPercent)} color="var(--orange)" /></div></div>
            {metric.gpus.length > 0 && <div className="grid md:grid-cols-2 gap-3">{metric.gpus.map((gpu) => <div className="card p-4" key={gpu.uuid ?? gpu.index}><div className="t-headline">GPU {gpu.index} · {gpu.name}</div><div className="grid grid-cols-3 gap-3 mt-3"><div><div className="t-caption">{t('GPU 利用率')}</div><div className="text-lg font-semibold">{gpu.utilization_percent?.toFixed(0) ?? '—'}%</div></div><div><div className="t-caption">{t('显存')}</div><div className="text-lg font-semibold">{gpu.memory_total_mib ? `${((gpu.memory_used_mib ?? 0) / gpu.memory_total_mib * 100).toFixed(0)}%` : '—'}</div></div><div><div className="t-caption">°C</div><div className="text-lg font-semibold">{gpu.temperature_c?.toFixed(0) ?? '—'}</div></div></div></div>)}</div>}
          </>}
        </>}
      </>}
    </div>
  </div>;
}
