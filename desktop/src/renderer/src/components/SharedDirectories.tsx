import {serverDisplayMessage} from '../serverDisplay';
import {useT} from '../i18n';
import { useEffect, useState } from 'react';
import { useApp } from '../store';
import { storageCall, type SharedRoot } from './RemoteData';
import type { PlatformNode } from '@shared/types';

export default function SharedDirectories() {
 const {t,lang}=useT();
  const { platform } = useApp();
  if (!platform.authenticated || !platform.user?.isAdmin) return null;
  return <Manager key={platform.user.id} />;
}

function Manager() {
 const {t,lang}=useT();
  const {platform} = useApp();
  const [roots,setRoots]=useState<SharedRoot[]>([]);
  const [nodes,setNodes]=useState<PlatformNode[]>([]);
  const [name,setName]=useState('');
  const [selected,setSelected]=useState('');
  const [nodeId,setNodeId]=useState('');
  const [localKey,setLocalKey]=useState('');
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  const root=roots.find(r=>r.id===selected);
  const load=async()=>{const [r,n]=await Promise.all([storageCall('roots'),window.biodsh.platformNodes()]);setRoots(r as SharedRoot[]);setNodes(n);};
  useEffect(()=>{void load().catch(error=>setNotice(String(error).replace(/^Error:\s*/,'')));},[]);
  const run=async(work:()=>Promise<unknown>)=>{setBusy(true);setNotice('');try{await work();await load();setNotice('已保存');}catch(error){setNotice(String(error).replace(/^Error:\s*/,''));}finally{setBusy(false);}};
  return <section className="card p-4 flex flex-col gap-3" aria-label={t("共享目录管理")}>
    <div className="t-headline">{t("共享目录")}</div>
    <div className="t-caption">{t("管理员登记服务器已批准的目录，再授权给项目。数据保留在原服务器。")}</div>
    <form className="flex gap-2" onSubmit={e=>{e.preventDefault();void run(async()=>{const r=await storageCall('create',{name:name.trim()}) as SharedRoot;setSelected(r.id);setName('');});}}>
      <input aria-label={t("新目录名称")} className="field flex-1" required maxLength={120} value={name} placeholder={t("例如：试点模拟数据")} onChange={e=>setName(e.target.value)}/><button className="btn btn-primary" disabled={busy || !name.trim()}>{t("登记目录")}</button>
    </form>
    <select aria-label={t("管理共享目录")} className="field" value={selected} disabled={busy} onChange={e=>{setSelected(e.target.value);setNodeId('');setLocalKey('');}}><option value="">{t("选择已登记目录")}</option>{roots.map(r=><option key={r.id} value={r.id}>{r.name}{r.enabled?'':t("（已停用）")}</option>)}</select>
    {root && <>
      <button className="btn btn-ghost self-start" disabled={busy} onClick={()=>void run(()=>storageCall('enable',{root_id:root.id,enabled:!root.enabled}))}>{root.enabled?t("停用共享目录"):t("启用共享目录")}</button>
      <div className="t-caption">{t("停用或取消项目授权仅撤销访问，不删除服务器文件。")}</div>
      <div className="t-body font-semibold">{t("服务器映射")}</div>
      {root.bindings.map(b=><div className="t-caption" key={b.node_id}>{b.node_name} · {b.local_key} · {b.status}<button className="btn btn-ghost" disabled={busy} onClick={()=>{setNodeId(b.node_id);setLocalKey(b.local_key || '');}}>{t("编辑")}</button></div>)}
      <form className="flex flex-wrap gap-2" onSubmit={e=>{e.preventDefault();void run(()=>storageCall('bind',{root_id:root.id,node_id:nodeId,local_key:localKey}));}}>
        <select aria-label={t("数据服务器")} className="field flex-1" required value={nodeId} onChange={e=>setNodeId(e.target.value)}><option value="">{t("选择已注册的服务器")}</option>{nodes.map(n=><option key={n.id} value={n.id}>{n.name}</option>)}</select>
        <input aria-label={t("目录配置编号")} className="field flex-1" required pattern="[a-zA-Z0-9_-]{1,80}" value={localKey} onChange={e=>setLocalKey(e.target.value)} placeholder={t("服务器目录配置编号")}/>
        <button className="btn btn-primary" disabled={busy || !nodeId || !localKey}>{t("保存映射")}</button>
      </form>
      <div className="t-caption">{t("目录配置编号由管理员在服务器上批准目录时设置。未配置的目录不能通过网页开放。")}</div>
      <div className="t-body font-semibold">{t("允许访问的项目")}</div>
      {platform.projects.map(project=><label className="flex gap-2 items-center" key={project.id}><input type="checkbox" checked={root.project_ids?.includes(project.id) || false} disabled={busy} onChange={e=>void run(()=>storageCall(e.target.checked?'grant':'revoke',{root_id:root.id,project_id:project.id}))}/>{project.name}</label>)}
    </>}
    <div role="status" className="t-caption">{serverDisplayMessage(t,notice || '')}</div>
  </section>;
}
