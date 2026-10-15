import { useEffect, useState } from 'react';
import { Activity, Cpu, HardDrive, MemoryStick, Check, KeyRound, Loader2, Pencil, Plus, Server, ShieldCheck, Trash2 } from 'lucide-react';
import type { SshHost, SshHostDraft, SshProbe, SshMetrics } from '@shared/types';
import { useApp } from '../store';
import { useT } from '../i18n';
import SudoInstall from '../components/SudoInstall';

const blank = (): SshHostDraft => ({ name: '', host: '', port: 22, username: '', auth: 'password' });

export default function HostsView() {
  const { t } = useT();
  const { currentSession, setTab } = useApp();
  const api = window.biodsh;
  const [hosts, setHosts] = useState<SshHost[]>([]);
  const [metrics, setMetrics] = useState<Record<string, SshMetrics>>({});
  const [resourceBusy, setResourceBusy] = useState<Record<string, boolean>>({});
  const [resourceErrors, setResourceErrors] = useState<Record<string, string>>({});
  const [autoRefresh, setAutoRefresh] = useState(false);
  const query = async (id: string) => {
    if (resourceBusy[id]) return;
    setResourceBusy(value => ({ ...value, [id]: true })); setResourceErrors(value => ({ ...value, [id]: '' }));
    try { const value = await api.sshHostMetrics(id); setMetrics(previous => ({ ...previous, [id]: value })); }
    catch (reason) { setResourceErrors(value => ({ ...value, [id]: String(reason).slice(0, 300) })); }
    finally { setResourceBusy(value => ({ ...value, [id]: false })); }
  };
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => { hosts.filter(host => host.hostKey).forEach(host => { void query(host.id); }); }, 30000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, hosts, resourceBusy]);
  const [draft, setDraft] = useState<SshHostDraft>(blank);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [setupHost,setSetupHost] = useState<SshHost|null>(null);
  useEffect(()=>{
    const finished=(event:Event)=>{const id=(event as CustomEvent<{id:string}>).detail.id;setSetupHost(previous=>previous?.id===id?null:previous);setError('');setNotice('安装与环境检查完成');};
    window.addEventListener('biodsh-install-finished',finished);
    return()=>window.removeEventListener('biodsh-install-finished',finished);
  },[]);
  const bootstrap = async(host:SshHost) => {setSetupHost(null);try {await api.personalCompute(host.id,'bootstrap');}catch(reason){setSetupHost(host);throw reason;}};
  const [pending, setPending] = useState<{ host: SshHost; probe: SshProbe } | null>(null);
  useEffect(() => { void api.sshHosts().then(setHosts).catch(reason => setError(String(reason))); }, []);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (reason) { setNotice(''); setError(String(reason).slice(0, 300)); }
    finally { setBusy(false); }
  };
  const update = <K extends keyof SshHostDraft>(key: K, value: SshHostDraft[K]) => setDraft(previous => ({ ...previous, [key]: value }));
  const edit = (host?: SshHost) => {
    setDraft(host ? { id: host.id, name: host.name, host: host.host, port: host.port, username: host.username, auth: host.auth } : blank());
    setEditing(true); setPending(null); setError(''); setNotice('');
  };
  const save = () => run(async () => {
    const next = { ...draft, password: draft.password || undefined, privateKey: draft.privateKey || undefined,
      passphrase: draft.privateKey ? draft.passphrase || '' : draft.passphrase || undefined };
    setHosts(await api.sshHostSave(next)); setDraft(blank()); setEditing(false); setNotice(t('服务器已保存'));
  });
  const test = (host: SshHost) => run(async () => {
    const probe = await api.sshHostTest(host.id, true);
    if (probe.hostKey !== host.hostKey) { setPending({ host, probe }); return; }
    await api.sshHostTest(host.id, false); setNotice('已连接，正在准备计算环境…'); await bootstrap(host); setNotice('连接与自动配置完成，可前往任务与调度。');
  });
  const trust = () => run(async () => {
    if (!pending) return;
    setHosts(await api.sshHostTrust(pending.host.id, pending.probe.hostKey, pending.probe.fingerprint));
    await api.sshHostTest(pending.host.id, false);
    setPending(null); setNotice('已连接，正在准备计算环境…'); await bootstrap(pending.host); setNotice('连接与自动配置完成，可前往任务与调度。');
  });
  const ask = () => run(async () => {
    if (!currentSession) throw new Error(t('请先新建或打开一个对话'));
    const selected = hosts.filter(host => host.selected);
    const content = `用户在服务器管理中选择了以下 SSH 服务器：${selected.map(host => `${host.name}（编号 ${host.id}）`).join('、')}。请使用 mcp__biodsh_ssh__list_servers 确认当前选择，通过 mcp__biodsh_ssh__get_resources 按服务器编号查询资源。任意 SSH 命令已禁用；计算需使用 mcp__biodsh_ssh__submit_task 用户级隔离任务，不得改用其他终端绕过。不要读取、询问或输出密码和私钥。请先说明这些服务器可用于哪些工作，等待我的具体任务。`;
    await api.dshRpc('session.prompt', { sessionId: currentSession, mode: 'queue', content: [{ type: 'text', text: content }] }); setTab('chat');
  });
  return <div className="h-full flex flex-col">
    <div className="flex-1 overflow-y-auto p-5"><div className="max-w-[1180px] mx-auto flex flex-col gap-4">
      <div className="flex flex-wrap gap-3 items-center"><div className="flex-1"><div className="t-title2">{t('我的服务器')}</div><div className="t-caption mt-1">{t('添加连接信息，选择允许智能体使用的服务器。')}</div></div><button className="btn btn-primary" disabled={busy} onClick={() => edit()}><Plus size={14} />{t('添加服务器')}</button></div>
      <div className="flex flex-wrap items-center gap-3"><button className="btn btn-ghost" onClick={() => hosts.filter(host => host.hostKey).forEach(host => { void query(host.id); })}>刷新全部资源</button><label className="t-caption flex gap-2 items-center"><input type="checkbox" checked={autoRefresh} onChange={event => setAutoRefresh(event.target.checked)} />每 30 秒自动刷新（页面打开期间）</label><span className="t-caption"></span></div>
      <section className="card p-4 flex flex-col gap-2" aria-label="数据保护"><div className="t-headline">连接后就能管理服务器</div><div className="t-caption">查看资源、安排分析、复制数据。分析前会检查保护环境，原文件只读，结果另存。</div></section>
      {error && <div role="alert" className="card p-3" style={{ color: 'var(--red)' }}>{error}</div>}
      {setupHost && <SudoInstall id={setupHost.id} label={`${setupHost.name} · ${setupHost.username}@${setupHost.host}`}/>}
      {notice && <div role="status" className="card p-3 flex items-center gap-2" style={{ color: 'var(--green)' }}><Check size={16} />{notice}</div>}
      {pending && <div className="card p-4 flex flex-col gap-3"><div className="t-headline">{pending.host.hostKey ? t('服务器主机指纹已变化') : t('首次连接：确认主机指纹')}</div><div>{pending.host.name} · {pending.host.host}:{pending.host.port}</div><code className="break-all">{pending.probe.fingerprint}</code><div className="t-caption">{t('请与服务器管理员核对指纹。确认后才会发送登录凭据。')}</div><div className="flex gap-2"><button className="btn btn-primary" disabled={busy} onClick={() => void trust()}>{t('信任此主机并测试连接')}</button><button className="btn btn-ghost" disabled={busy} onClick={() => setPending(null)}>{t('取消')}</button></div></div>}
      <div className={`grid gap-4 ${editing ? 'lg:grid-cols-[1fr_380px]' : ''}`}>
        <div><div className="grid md:grid-cols-2 gap-3">{hosts.map(host => <div key={host.id} className="card p-4 flex flex-col gap-3" style={{ borderColor: host.selected ? 'var(--accent)' : undefined }}>
          <div className="flex items-center gap-3"><div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}><Server size={23} /></div><div className="flex-1 min-w-0"><div className="t-headline truncate">{host.name}</div><div className="t-caption truncate">{host.username}@{host.host}:{host.port}</div></div><button aria-label={t('编辑服务器')} className="p-2" disabled={busy} onClick={() => edit(host)}><Pencil size={15} /></button></div>
          <div className="flex gap-2 t-caption items-center"><KeyRound size={13} />{host.auth === 'password' ? t('密码认证') : t('私钥认证')}{host.fingerprint && <ShieldCheck size={14} style={{ color: 'var(--green)' }} />}</div>
          <label className="flex items-center gap-2 cursor-pointer"><input type="checkbox" checked={host.selected} disabled={busy || !host.hostKey} onChange={event => { const selected = event.target.checked; void run(async () => { setHosts(await api.sshHostSelect(host.id, selected)); }); }} />{t('允许智能体使用')}</label>
          <div className="flex gap-2"><button className="btn btn-ghost flex-1" disabled={busy} onClick={() => void test(host)}>{t('测试连接')}</button><button aria-label={t('删除服务器')} className="btn btn-ghost !px-2" disabled={busy} onClick={() => { if (window.confirm(t('删除连接配置？服务器上的文件不会被删除。'))) void run(async () => { setHosts(await api.sshHostRemove(host.id)); if (draft.id === host.id) { setDraft(blank()); setEditing(false); } }); }}><Trash2 size={14} /></button></div>
          <button className="btn btn-ghost" disabled={!host.hostKey || resourceBusy[host.id]} onClick={() => void query(host.id)}>{resourceBusy[host.id] ? '正在查询资源…' : '查询 / 刷新资源'}</button>
          {resourceErrors[host.id] && <div role="alert" className="t-caption break-words" style={{ color: 'var(--red)' }}>{resourceErrors[host.id]}</div>}
          {metrics[host.id] && <ResourceSummary value={metrics[host.id]} />}
        </div>)}</div>{hosts.length === 0 && <div className="card p-10 text-center"><Server size={32} className="mx-auto mb-3" /><div className="t-headline">{t('还没有服务器')}</div><div className="t-caption mt-2">{t('填写名字、IP、端口和登录方式即可添加。')}</div></div>}
        <div className="card p-4 mt-4 flex flex-col gap-3"><div className="t-headline">{t('智能体连接范围')} · {hosts.filter(host => host.selected).length}</div><div className="t-caption">{t('勾选可同时使用多台服务器。取消勾选后，智能体不能再建立新的连接；已启动的远程任务不会自动停止。')}</div><button className="btn btn-primary self-start" disabled={busy || !hosts.some(host => host.selected)} onClick={() => void ask()}>{t('在对话中使用所选服务器')}</button></div></div>

        {editing && <form className="card p-4 flex flex-col gap-3 self-start" onSubmit={event => { event.preventDefault(); void save(); }}>
          <div className="t-headline">{draft.id ? t('编辑服务器') : t('添加服务器')}</div>
          <label>{t('自定义名字')}<input className="field w-full mt-1" required maxLength={120} value={draft.name} onChange={event => update('name', event.target.value)} /></label>
          <label>{t('IP 或主机名')}<input className="field w-full mt-1" required value={draft.host} onChange={event => update('host', event.target.value.trim())} /></label>
          <div className="grid grid-cols-[100px_1fr] gap-3"><label>{t('SSH 端口')}<input className="field w-full mt-1" type="number" required min={1} max={65535} value={draft.port} onChange={event => update('port', Number(event.target.value))} /></label><label>{t('用户名')}<input className="field w-full mt-1" required autoComplete="off" value={draft.username} onChange={event => update('username', event.target.value)} /></label></div>
          <label>{t('认证方式')}<select className="field w-full mt-1" value={draft.auth} onChange={event => update('auth', event.target.value as 'password' | 'key')}><option value="password">{t('密码认证')}</option><option value="key">{t('私钥认证')}</option></select></label>
          {draft.auth === 'password' ? <label>{t('密码')}<input className="field w-full mt-1" type="password" autoComplete="new-password" placeholder={draft.id ? t('留空保留已保存的凭据') : ''} value={draft.password || ''} onChange={event => update('password', event.target.value)} /></label> : <><label>{t('SSH 私钥')}<textarea className="field w-full mt-1 min-h-[140px] font-mono" autoComplete="off" placeholder={draft.id ? t('留空保留已保存的凭据') : '-----BEGIN OPENSSH PRIVATE KEY-----'} value={draft.privateKey || ''} onChange={event => update('privateKey', event.target.value)} /></label><label className="btn btn-ghost cursor-pointer">{t('从文件导入私钥')}<input className="hidden" type="file" onChange={event => { const file = event.target.files?.[0]; if (file) void run(async () => { if (file.size > 131072) throw new Error(t('私钥文件过大')); update('privateKey', await file.text()); }); }} /></label><label>{t('私钥口令（可选）')}<input type="password" className="field w-full mt-1" autoComplete="new-password" value={draft.passphrase || ''} onChange={event => update('passphrase', event.target.value)} /></label></>}
          <div className="t-caption">{t('密码、私钥和口令保存在系统安全存储，不发送给模型。')}</div>
          <div className="flex gap-2"><button type="submit" className="btn btn-primary flex-1" disabled={busy}>{busy && <Loader2 size={14} className="spin" />}{t('保存')}</button><button type="button" className="btn btn-ghost" disabled={busy} onClick={() => { setDraft(blank()); setEditing(false); }}>{t('取消')}</button></div>
        </form>}
      </div>
    </div></div>
  </div>;
}

const bytes = (value: number) => value >= 1073741824 ? (value / 1073741824).toFixed(1) + ' GiB' : (value / 1048576).toFixed(1) + ' MiB';
function ResourceMeter({name, percent}: {name:string; percent:number|null}) {
 const value = percent === null || !Number.isFinite(percent) ? null : Math.min(100, Math.max(0, percent));
 const color = value !== null && value >= 90 ? 'var(--red)' : value !== null && value >= 75 ? 'var(--orange)' : 'var(--accent)';
 return <div role="meter" aria-label={name} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined} aria-valuetext={value === null ? '暂无数据' : `${value.toFixed(1)}%`} className="h-2 rounded-full overflow-hidden" style={{background:'var(--fill)'}}><div className="h-full rounded-full transition-all duration-300" style={{width:`${value ?? 0}%`,background:color}} /></div>;
}
export function ResourceSummary({ value: m }: { value: SshMetrics }) {
 const ratio = (used:number,total:number) => total > 0 ? used / total * 100 : null;
 const cards = [
  {name:'CPU 占用',icon:Cpu,percent:m.cpuPercent,detail:`${m.logicalCores} 逻辑核 · ${m.physicalCores ?? '—'} 物理核`,footer:m.cpuModel},
  {name:'内存',icon:MemoryStick,percent:ratio(m.memoryUsed,m.memoryTotal),detail:`已用 ${bytes(m.memoryUsed)} / ${bytes(m.memoryTotal)}`,footer:`可用 ${bytes(Math.max(0,m.memoryTotal-m.memoryUsed))}`},
  {name:'交换内存',icon:HardDrive,percent:ratio(m.swapUsed,m.swapTotal),detail:m.swapTotal>0?`已用 ${bytes(m.swapUsed)} / ${bytes(m.swapTotal)}`:'此服务器未配置交换内存',footer:m.swapTotal>0?`剩余 ${bytes(Math.max(0,m.swapTotal-m.swapUsed))}`:'未启用'},
 ];
 return <section aria-label="服务器资源概览" className="flex flex-col gap-3 pt-3 hairline-t min-w-0">
  <div className="flex flex-wrap justify-between items-center gap-2"><div className="flex items-center gap-2 t-headline"><Activity size={15} style={{color:'var(--accent)'}}/>资源概览</div><span className="t-caption">采样于 {new Date(m.sampledAt*1000).toLocaleTimeString()}</span></div>
  <div className="grid gap-3" style={{gridTemplateColumns:'repeat(auto-fit, minmax(min(100%, 210px), 1fr))'}}>
   {cards.map(({name,icon:Icon,percent,detail,footer})=><div key={name} className="rounded-2xl p-4 min-w-0 flex flex-col gap-3" style={{background:'var(--surface)',border:'1px solid var(--border)'}}>
    <div className="flex items-center gap-2 t-caption"><Icon size={16} style={{color:'var(--accent)'}}/>{name}</div>
    <div className="text-[28px] leading-none font-semibold tabular-nums tracking-tight">{percent===null?'—':percent.toFixed(1)}{percent!==null&&<span className="text-sm ml-1 font-normal" style={{color:'var(--text-2)'}}>%</span>}</div>
    <ResourceMeter name={name} percent={percent}/>
    <div className="text-xs leading-5 tabular-nums">{detail}</div>
    <div className="t-caption truncate" title={footer}>{footer}</div>
   </div>)}
  </div>
  <details className="rounded-2xl overflow-hidden" style={{background:'var(--surface)',border:'1px solid var(--border)'}}>
   <summary className="cursor-pointer px-4 py-3 text-sm select-none">系统与硬件详情 <span className="t-caption ml-2">磁盘 · GPU · 系统信息</span></summary>
   <div className="p-4 pt-0 flex flex-col gap-5 min-w-0">
    <div className="grid gap-3 text-xs" style={{gridTemplateColumns:'repeat(auto-fit, minmax(min(100%, 180px), 1fr))'}}>
     {[['主机',m.hostname],['系统',`${m.system} · ${m.architecture}`],['运行时长',`${(m.uptimeSeconds/86400).toFixed(1)} 天`],['负载 · 1 / 5 / 15 分钟',m.load.map(v=>v.toFixed(2)).join(' / ')]].map(([label,text])=><div key={label} className="min-w-0"><div className="t-caption mb-1">{label}</div><div className="break-words">{text}</div></div>)}
    </div>
    <div><div className="t-headline mb-3">磁盘空间</div><div className="flex flex-col gap-4">{m.disks.length?m.disks.map(d=><div key={d.mount} className="flex flex-col gap-2"><div className="flex flex-wrap justify-between gap-2 text-xs"><span className="font-medium break-all">{d.mount}</span><span className="tabular-nums" style={{color:'var(--text-2)'}}>已用 {bytes(d.used)} / {bytes(d.total)}</span></div><ResourceMeter name={`磁盘 ${d.mount}`} percent={ratio(d.used,d.total)}/><div className="t-caption">可用 {bytes(d.free)}</div></div>):<div className="t-caption">暂无磁盘数据</div>}</div></div>
    <div><div className="t-headline mb-2">GPU</div>{m.gpu?<><div className="t-caption mb-2">名称 / 使用率 % / 显存总量 MiB / 已用 MiB / 温度 °C</div><pre className="text-xs leading-6 whitespace-pre-wrap break-words rounded-xl p-3" style={{background:'var(--fill)'}}>{m.gpu}</pre></>:<div className="t-caption">未检测到可查询的 NVIDIA GPU</div>}</div>
   </div>
  </details>
 </section>;
}
