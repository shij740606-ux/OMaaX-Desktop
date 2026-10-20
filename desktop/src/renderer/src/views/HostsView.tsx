import { useEffect, useRef, useState } from 'react';
import { Activity, Cpu, HardDrive, MemoryStick, Check, KeyRound, Loader2, Pencil, Plus, Server, ShieldCheck, Trash2 } from 'lucide-react';
import type { SshHost, SshHostDraft, SshProbe, SshMetrics } from '@shared/types';
import { useApp } from '../store';
import { useT } from '../i18n';
import HelpTip from '../components/HelpTip';
import SudoInstall from '../components/SudoInstall';

import {invalidateServer} from '../serverRequests';
import {serverError} from '../serverErrors';
import {useServerConnections,updateConnection,prepareServer,resetConnection,connectionRevision} from '../serverConnections';

const blank = (): SshHostDraft => ({ name: '', host: '', port: 22, username: '', auth: 'password' });
type HostUi={busy?:boolean;error?:string;notice?:string;install?:boolean;pending?:{host:SshHost;probe:SshProbe;selectAfter:boolean}|null};

export default function HostsView(_props:{active?:boolean}={}) {
  const connections=useServerConnections();
  const cards=useRef(new Map<string,HTMLDivElement>());
  const { t } = useT();
  const { currentSession, setTab } = useApp();
  const api = window.biodsh;
  const [hosts, setHosts] = useState<SshHost[]>([]);
  const [draft, setDraft] = useState<SshHostDraft>(blank);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [hostUi,setHostUi]=useState<Record<string,HostUi>>({});
  const activeHosts=useRef(new Set<string>());
  const patchHost=(id:string,patch:Partial<HostUi>)=>setHostUi(previous=>({...previous,[id]:{...previous[id],...patch}}));
  // Mutation replies contain a whole-list snapshot; only merge the server that was changed.
  const applyHostRows=(id:string,rows:SshHost[])=>setHosts(previous=>{
    const changed=rows.find(host=>host.id===id);
    if(!changed)return previous.filter(host=>host.id!==id);
    return previous.some(host=>host.id===id)?previous.map(host=>host.id===id?changed:host):[...previous,changed];
  });
  useEffect(()=>{
    const finished=(event:Event)=>{const id=(event as CustomEvent<{id:string}>).detail.id;patchHost(id,{install:false,notice:'后台环境配置已完成，请查看对应服务器状态'});};
    window.addEventListener('biodsh-install-finished',finished);
    return()=>window.removeEventListener('biodsh-install-finished',finished);
  },[]);
  const bootstrap = async(host:SshHost,retry=false) => {patchHost(host.id,{install:false});await prepareServer(host.id,retry);};
  const [focusHost,setFocusHost]=useState('');
  useEffect(()=>{if(focusHost&&_props.active!==false)cards.current.get(focusHost)?.scrollIntoView({block:'nearest',behavior:'smooth'});},[focusHost,_props.active]);
  useEffect(() => { void api.sshHosts().then(setHosts).catch(reason => setError(serverError(reason))); }, []);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (reason) { setNotice(''); setError(serverError(reason)); }
    finally { setBusy(false); }
  };
  const runHost=async(id:string,action:()=>Promise<void>)=>{
    if(activeHosts.current.has(id))return;
    activeHosts.current.add(id);patchHost(id,{busy:true,error:'',notice:''});
    try{await action();}catch(reason){patchHost(id,{error:serverError(reason,'connection'),notice:''});}
    finally{activeHosts.current.delete(id);patchHost(id,{busy:false});}
  };
  const update = <K extends keyof SshHostDraft>(key: K, value: SshHostDraft[K]) => setDraft(previous => ({ ...previous, [key]: value }));
  const edit = (host?: SshHost) => {
    setDraft(host ? { id: host.id, name: host.name, host: host.host, port: host.port, username: host.username, auth: host.auth } : blank());
    setEditing(true); setError(''); setNotice('');
  };
  const clearHostState=(id:string)=>{resetConnection(id);invalidateServer(id);};
  const authenticate=async(host:SshHost,selectAfter=false)=>{
    const revision=connectionRevision(host.id);
    updateConnection(host.id,{network:'verified',authentication:'checking',error:''});
    try{
      await api.sshHostTest(host.id,false);
      if(revision!==connectionRevision(host.id))throw Error('连接信息已变化，请重新验证');
      updateConnection(host.id,{authentication:'verified',checkedAt:Date.now(),error:''});
    }catch(reason){if(revision===connectionRevision(host.id))updateConnection(host.id,{authentication:'failed',error:String(reason)});throw reason;}
    if(selectAfter)applyHostRows(host.id,await api.sshHostSelect(host.id,true));
    patchHost(host.id,{notice:selectAfter?'SSH 登录通过，已允许智能体使用。基础环境单独准备。':'登录验证通过，连接已保存。可以浏览服务器文件；分析环境在后台按需准备。'});void bootstrap(host);
  };
  const connect=async(host:SshHost,selectAfter=false)=>{
    const revision=connectionRevision(host.id);
    patchHost(host.id,{pending:null});updateConnection(host.id,{network:'testing',authentication:'unknown',error:''});
    let probe:SshProbe;
    try{probe=await api.sshHostTest(host.id,true);}
    catch(reason){if(revision===connectionRevision(host.id))updateConnection(host.id,{network:'failed',error:String(reason)});throw reason;}
    if(revision!==connectionRevision(host.id))throw Error('连接信息已变化，请重新验证');
    updateConnection(host.id,{network:'verified'});
    if(!probe.hostKey||!probe.fingerprint)throw Error('未取得服务器主机指纹，请重试');
    if(probe.hostKey!==host.hostKey){patchHost(host.id,{pending:{host,probe,selectAfter}});setFocusHost(host.id);updateConnection(host.id,{authentication:'awaiting_trust'});return;}
    await authenticate(host,selectAfter);
  };
  const save=()=>run(async()=>{
    const next={...draft,password:draft.password||undefined,privateKey:draft.privateKey||undefined,passphrase:draft.privateKey?draft.passphrase||'':draft.passphrase||undefined};
    const oldIds=new Set(hosts.map(h=>h.id));
    const rows=await api.sshHostSave(next),saved=rows.find(h=>next.id?h.id===next.id:!oldIds.has(h.id));
    if(!saved)throw Error('连接已保存，但未找到连接记录，请刷新列表');
    applyHostRows(saved.id,rows);setDraft(blank());setEditing(false);
    clearHostState(saved.id);void runHost(saved.id,()=>connect(saved));
  });
  const test=(host:SshHost)=>runHost(host.id,()=>connect(host));
  const trust=(id:string)=>runHost(id,async()=>{
    const pending=hostUi[id]?.pending;
    if(!pending)return;const {host,probe,selectAfter}=pending;
    const latest=(await api.sshHosts()).find(h=>h.id===host.id);
    if(!latest||latest.host!==host.host||latest.port!==host.port||latest.username!==host.username)throw Error('连接信息已变化，请重新测试连接');
    const rows=await api.sshHostTrust(host.id,probe.hostKey,probe.fingerprint),trusted=rows.find(h=>h.id===host.id);
    if(!trusted)throw Error('服务器记录已移除');
    applyHostRows(id,rows);patchHost(id,{pending:null});await authenticate(trusted,selectAfter);
  });
  const select=(host:SshHost,selected:boolean)=>runHost(host.id,async()=>{
    if(!selected){applyHostRows(host.id,await api.sshHostSelect(host.id,false));return;}
    if(host.hostKey&&connections[host.id]?.authentication==='verified')applyHostRows(host.id,await api.sshHostSelect(host.id,true));
    else await connect(host,true);
  });
  const ask = () => run(async () => {
    if (!currentSession) throw new Error(t('请先新建或打开一个对话'));
    const selected = hosts.filter(host => host.selected);
    const content = `请使用我已选择的服务器：${selected.map(host => host.name).join('、')}。先介绍可用资源和适合开展的分析，等待我的具体任务。`;
    await api.dshRpc('session.prompt', { sessionId: currentSession, mode: 'queue', content: [{ type: 'text', text: content }] }); setTab('chat');
  });
  return <div className="h-full flex flex-col">
    <div className="flex-1 overflow-y-auto p-5"><div className="max-w-[1180px] mx-auto flex flex-col gap-4">
      <div className="flex flex-wrap gap-3 items-center"><div className="flex-1"><div className="t-title2">{t('我的服务器')}</div><div className="t-caption mt-1">{t('添加连接信息，选择允许智能体使用的服务器。')}</div></div><button className="btn btn-primary" disabled={busy} onClick={() => edit()}><Plus size={14} />{t('添加服务器')}</button></div>
      <section className="card p-4 flex flex-col gap-2" aria-label="数据保护"><div className="t-headline">连接后就能管理服务器</div><div className="t-caption">首次登录后自动创建 ~/omaax 并按需准备基础环境。SSH 连接、账号认证和环境状态分别显示；环境失败不会影响已验证的 SSH 登录。分析结果按项目和对话分别保存。</div></section>
      {error && <div role="alert" className="card p-3" style={{ color: 'var(--red)' }}>{error}</div>}
      {notice && <div role="status" className="card p-3 flex items-center gap-2" style={{ color: 'var(--green)' }}><Check size={16} />{notice}</div>}
      <div className={`grid gap-4 ${editing ? 'lg:grid-cols-[1fr_380px]' : ''}`}>
        <div><div className="grid md:grid-cols-2 gap-3">{hosts.map(host => {const state=hostUi[host.id]||{},pending=state.pending,cardBusy=Boolean(state.busy||(busy&&draft.id===host.id));return <div key={host.id} aria-label={'服务器 '+host.name} ref={node=>{if(node)cards.current.set(host.id,node);else cards.current.delete(host.id);}} className="card p-4 flex flex-col gap-3" style={{ borderColor: host.selected ? 'var(--accent)' : undefined }}>
          <div className="flex items-center gap-3"><div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}><Server size={23} /></div><div className="flex-1 min-w-0"><div className="t-headline truncate">{host.name}</div><div className="t-caption truncate">{host.username}@{host.host}:{host.port}</div></div><button aria-label={t('编辑服务器')} className="p-2" disabled={cardBusy} onClick={() => edit(host)}><Pencil size={15} /></button></div>
          <div className="flex gap-2 t-caption items-center"><KeyRound size={13} />{host.auth === 'password' ? t('密码认证') : t('私钥认证')}{host.fingerprint && <ShieldCheck size={14} style={{ color: 'var(--green)' }} />}</div>
          {connections[host.id]&&<section className="rounded-xl p-3 flex flex-col gap-2" style={{background:'var(--fill)'}}>
            <div className="text-sm">连接：{{unknown:'尚未验证',testing:'正在连接',verified:'已验证可达',failed:'连接失败'}[connections[host.id].network]} · 认证：{{unknown:'尚未验证',checking:'正在验证',awaiting_trust:'等待确认主机指纹',verified:'已通过',failed:'未通过'}[connections[host.id].authentication]}</div>
            {connections[host.id].checkedAt&&<div className="t-caption">上次验证：{new Date(connections[host.id].checkedAt!).toLocaleString()}</div>}
            <div className="text-sm">基础环境：{connections[host.id].setup?.detail||'登录验证后自动准备'}</div>
            {connections[host.id].setup?.errorDetail&&<details className="t-caption"><summary>技术详情</summary><pre className="whitespace-pre-wrap break-all mt-2">{connections[host.id].setup?.errorDetail}</pre></details>}
            {connections[host.id].workspace&&<div className="t-caption break-all">专属工作目录：{connections[host.id].workspace}</div>}
            {connections[host.id].setup&&['failed','interrupted'].includes(connections[host.id].setup!.state)&&<div className="flex flex-wrap gap-2"><button className="btn btn-tint" disabled={cardBusy} onClick={()=>void bootstrap(host,true)}>重试环境准备</button><button className="btn btn-ghost" disabled={cardBusy} onClick={()=>patchHost(host.id,{install:true})}>准备用户环境</button></div>}
          </section>}
          {pending && <div className="card p-4 flex flex-col gap-3"><div className="t-headline">{pending.host.hostKey ? t('服务器主机指纹已变化') : t('首次连接：确认主机指纹')}</div><div>{pending.host.name} · {pending.host.host}:{pending.host.port}</div><code className="break-all">{pending.probe.fingerprint}</code><div className="t-caption">{t('请与服务器管理员核对指纹。确认后才会发送登录凭据。')}</div><div className="flex gap-2"><button className="btn btn-primary" disabled={cardBusy} onClick={() => void trust(host.id)}>{t('信任此主机并测试连接')}</button><button className="btn btn-ghost" disabled={cardBusy} onClick={() => {patchHost(host.id,{pending:null});updateConnection(host.id,{authentication:'unknown',error:''});}}>{t('取消')}</button></div></div>}
          {state.install&&<SudoInstall id={host.id} label={`${host.name} · ${host.username}@${host.host}`}/>}
          {(state.error||connections[host.id]?.error)&&<p role="alert" className="t-caption break-words" style={{color:'var(--red)'}}>{state.error||connections[host.id].error}</p>}
          {state.notice&&<p role="status" className="t-caption" style={{color:'var(--green)'}}>{state.notice}</p>}
          <label className="flex items-center gap-2 cursor-pointer"><input type="checkbox" aria-label={'允许智能体使用 '+host.name} checked={host.selected} disabled={cardBusy} onChange={event=>void select(host,event.target.checked)}/>{t('允许智能体使用')}</label>
          {!host.hostKey&&!pending&&<p className="t-caption">勾选后会先连接，并在这张卡片确认服务器指纹与密码。</p>}
          <div className="flex gap-2"><button className="btn btn-ghost flex-1" disabled={cardBusy} onClick={() => void test(host)}>{cardBusy?<><Loader2 size={14} className="spin"/>{t('正在处理…')}</>:t('测试连接')}</button><button aria-label={t('删除服务器')} className="btn btn-ghost !px-2" disabled={cardBusy} onClick={() => { if (window.confirm(t('删除连接配置？服务器上的文件不会被删除。'))) void runHost(host.id,async () => { applyHostRows(host.id,await api.sshHostRemove(host.id));clearHostState(host.id); if (draft.id === host.id) { setDraft(blank()); setEditing(false); } }); }}><Trash2 size={14} /></button></div>
        </div>;})}</div>{hosts.length === 0 && <div className="card p-10 text-center"><Server size={32} className="mx-auto mb-3" /><div className="t-headline">{t('还没有服务器')}</div><div className="t-caption mt-2">{t('填写名字、IP、端口和登录方式即可添加。')}</div></div>}
        <div className="card p-4 mt-4 flex flex-col gap-3"><div className="t-headline">{t('智能体连接范围')} · {hosts.filter(host => host.selected).length}</div><div className="t-caption">{t('勾选可同时使用多台服务器。取消勾选后，智能体不能再建立新的连接；已启动的远程任务不会自动停止。')}</div><button className="btn btn-primary self-start" disabled={busy || !hosts.some(host => host.selected)} onClick={() => void ask()}>{t('在对话中使用所选服务器')}</button></div></div>

        {editing && <form className="card p-4 flex flex-col gap-3 self-start" onSubmit={event => { event.preventDefault(); void save(); }}>
          <div className="t-headline">{draft.id ? t('编辑服务器') : t('添加服务器')}</div>
          <label>{t('自定义名字')}<input className="field w-full mt-1" required maxLength={120} value={draft.name} onChange={event => update('name', event.target.value)} /></label>
          <label>{t('IP 或主机名')}<input className="field w-full mt-1" required value={draft.host} onChange={event => update('host', event.target.value.trim())} /></label>
          <div className="grid grid-cols-[100px_1fr] gap-3"><label>{t('SSH 端口')}<input className="field w-full mt-1" type="number" required min={1} max={65535} value={draft.port} onChange={event => update('port', Number(event.target.value))} /></label><label>{t('用户名')}<input className="field w-full mt-1" required autoComplete="off" value={draft.username} onChange={event => update('username', event.target.value)} /></label></div>
          <label>{t('认证方式')}<select className="field w-full mt-1" value={draft.auth} onChange={event => update('auth', event.target.value as 'password' | 'key')}><option value="password">{t('密码认证')}</option><option value="key">{t('私钥认证')}</option></select></label>
          {draft.auth === 'password' ? <label>{t('密码')}<input className="field w-full mt-1" type="password" autoComplete="new-password" placeholder={draft.id ? t('留空保留已保存的凭据') : ''} value={draft.password || ''} onChange={event => update('password', event.target.value)} /></label> : <><label>{t('SSH 私钥')}<textarea className="field w-full mt-1 min-h-[140px] font-mono" autoComplete="off" placeholder={draft.id ? t('留空保留已保存的凭据') : '-----BEGIN OPENSSH PRIVATE KEY-----'} value={draft.privateKey || ''} onChange={event => update('privateKey', event.target.value)} /></label><label className="btn btn-ghost cursor-pointer">{t('从文件导入私钥')}<input className="hidden" type="file" onChange={event => { const file = event.target.files?.[0]; if (file) void run(async () => { if (file.size > 131072) throw new Error(t('私钥文件过大')); update('privateKey', await file.text()); }); }} /></label><label>{t('私钥口令（可选）')}<input type="password" className="field w-full mt-1" autoComplete="new-password" value={draft.passphrase || ''} onChange={event => update('passphrase', event.target.value)} /></label></>}
          <div className="t-caption">{t('密码、私钥和口令保存在系统安全存储，不发送给模型。')}</div>
          <div className="flex gap-2"><button type="submit" className="btn btn-primary flex-1" disabled={busy}>{busy && <Loader2 size={14} className="spin" />}{t('保存并连接')}</button><button type="button" className="btn btn-ghost" disabled={busy} onClick={() => { setDraft(blank()); setEditing(false); }}>{t('取消')}</button></div>
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
  {name:'CPU 占用',topic:'cpu' as const,icon:Cpu,percent:m.cpuPercent,detail:`${m.logicalCores} 逻辑核 · ${m.physicalCores ?? '—'} 物理核`,footer:m.cpuModel},
  {name:'运行内存',topic:'memory' as const,icon:MemoryStick,percent:ratio(m.memoryUsed,m.memoryTotal),detail:`已用 ${bytes(m.memoryUsed)} / ${bytes(m.memoryTotal)}`,footer:`可用 ${bytes(Math.max(0,m.memoryTotal-m.memoryUsed))}`},
  {name:'交换空间',topic:'swap' as const,icon:HardDrive,percent:ratio(m.swapUsed,m.swapTotal),detail:m.swapTotal>0?`已用 ${bytes(m.swapUsed)} / ${bytes(m.swapTotal)}`:'此服务器未配置交换空间',footer:m.swapTotal>0?`剩余 ${bytes(Math.max(0,m.swapTotal-m.swapUsed))}`:'未启用'},
 ];
 return <section aria-label="服务器资源概览" className="flex flex-col gap-3 pt-3 hairline-t min-w-0">
  <div className="flex flex-wrap justify-between items-center gap-2"><div className="flex items-center gap-2 t-headline"><Activity size={15} style={{color:'var(--accent)'}}/>服务器当前用量 <HelpTip topic="resources"/></div><span className="t-caption">采样于 {new Date(m.sampledAt*1000).toLocaleTimeString()}</span></div>
  <div className="grid gap-3" style={{gridTemplateColumns:'repeat(auto-fit, minmax(min(100%, 210px), 1fr))'}}>
   {cards.map(({name,topic,icon:Icon,percent,detail,footer})=><div key={name} className="omaax-resource-card">
    <div className="omaax-label-row"><span className="flex items-center gap-2"><Icon size={16}/>{name}</span><HelpTip topic={topic} align="right"/></div>
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
