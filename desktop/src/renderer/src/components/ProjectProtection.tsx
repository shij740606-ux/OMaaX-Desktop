import {serverDisplayMessage} from '../serverDisplay';
import {useT} from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { useApp } from '../store';
import { storageCall } from './RemoteData';

interface Document {id:string;name:string;revision:number;content:string;deleted:boolean}
interface Preview {id:string;digest:string;before:Document;after:{content:string;operation:string};expires_at:string}
interface Backup {id:string;snapshot_id:string;created_at:string}

export default function ProjectProtection() {
 const {t,lang}=useT();
  const {settings,platform} = useApp();
  const project = settings?.currentProjectId || '';
  const generation = useRef(0);
  const [files,setFiles] = useState<Document[]>([]);
  const [backups,setBackups] = useState<Backup[]>([]);
  const [configured,setConfigured] = useState(false);
  const [selected,setSelected] = useState('');
  const [name,setName] = useState('');
  const [content,setContent] = useState('');
  const [preview,setPreview] = useState<Preview|null>(null);
  const [notice,setNotice] = useState('');
  const [busy,setBusy] = useState(false);
  const current = files.find(f=>f.id===selected);
  const owner = platform.user?.isAdmin || platform.projects.find(p=>p.id===project)?.role==='owner';
  const reload = async (version:number) => {
    const [f,b,s] = await Promise.all(['files','backups','protection'].map(action=>storageCall(action,{project_id:project})));
    if(generation.current!==version)return;
    setFiles(f as Document[]); setBackups(b as Backup[]);
    setConfigured((s as {backup:{configured:boolean}}).backup.configured);
  };
  useEffect(()=>{const version=++generation.current;setPreview(null);setFiles([]);setBackups([]);setSelected('');setContent('');setNotice('');setConfigured(false);setBusy(false);setName('');
    if(project) void reload(version).catch(error=>generation.current===version && setNotice(String(error).replace(/^Error:\s*/,'')));
    return ()=>{generation.current++;};
  },[project]);
  const action = async (fn:(alive:()=>boolean)=>Promise<void>) => {const version=generation.current;setBusy(true);setNotice('');try{await fn(()=>generation.current===version);if(generation.current===version)await reload(version);}catch(error){if(generation.current===version)setNotice(String(error).replace(/^Error:\s*/,''));}finally{if(generation.current===version)setBusy(false);}};
  if(!project || !platform.authenticated)return null;
  return <section className="card p-4 flex flex-col gap-3" aria-label={t("项目文件保护")}>
    <h3 className="t-headline">{t("项目文件保护")}</h3>
    <p className="t-caption">{t("管理分析说明、参数和脚本等项目文件。原始数据始终只读；退役仅隐藏当前文件，历史版本保留。")}</p>
    <p role="status" className="t-caption">{configured?t("备份已配置；每次提交均验证备份读回结果。"):t("尚未配置独立备份：可以预览，禁止提交修改。")}</p>
    {owner && <div className="flex gap-2"><input className="field flex-1" aria-label={t("新项目文件名")} placeholder={t("例如 analysis-notes.txt")} value={name} onChange={e=>setName(e.target.value)}/><button className="btn btn-ghost" disabled={busy||!name.trim()} onClick={()=>void action(async(alive)=>{await storageCall('file-create',{project_id:project,name:name.trim()});if(alive())setName('');})}>{t("建立项目文件")}</button></div>}
    <select aria-label={t("受保护项目文件")} className="field" value={selected} disabled={busy} onChange={e=>{setSelected(e.target.value);setContent(files.find(f=>f.id===e.target.value)?.content||'');setPreview(null);}}><option value="">{t("选择文件")}</option>{files.map(f=><option key={f.id} value={f.id}>{t(f.name || '')} {t("· 版本")}{f.revision}{f.deleted?t("（已退役）"):''}</option>)}</select>
    {current && <><textarea aria-label={t("新版本内容")} className="field min-h-[140px]" disabled={busy||current.deleted} value={content} onChange={e=>{setContent(e.target.value);setPreview(null);}}/>
      <div className="flex gap-2">{['replace','retire'].map(operation=><button className="btn btn-ghost" key={operation} disabled={busy||current.deleted} onClick={()=>void action(async()=>{const version=generation.current;const p=await storageCall('preview',{project_id:project,file_id:current.id,change:{operation,expected_revision:current.revision,content:operation==='retire'?'':content}});if(version===generation.current)setPreview(p as Preview);})}>{operation==='replace'?t("预览新版本"):t("预览退役")}</button>)}</div></>}
    {preview && <div className="p-3 border rounded-xl"><div className="t-headline">{t("确认变更：")}{preview.before.name}</div><div className="t-caption">{t("原版本")}{preview.before.revision} · {preview.after.operation==='retire'?t("退役，保留历史"):t("创建新版本")} {t("· 有效至")}{new Date(preview.expires_at).toLocaleTimeString()}</div>
      <div className="grid grid-cols-2 gap-3"><div><div className="t-caption">{t("变更前")}</div><pre className="whitespace-pre-wrap max-h-52 overflow-auto">{preview.before.content||t("（空）")}</pre></div><div><div className="t-caption">{t("变更后")}</div><pre className="whitespace-pre-wrap max-h-52 overflow-auto">{preview.after.content||t("（空）")}</pre></div></div>
      <button className="btn btn-primary" disabled={!owner||!configured||busy} onClick={()=>void action(async(alive)=>{const approved=await storageCall('approve',{project_id:project,change_id:preview.id,digest:preview.digest}) as {approval_token:string};if(!alive())return;await storageCall('commit',{project_id:project,change_id:preview.id,approval_token:approved.approval_token});if(alive()){setPreview(null);setNotice("已备份并保存新版本。");}})}>{t("确认并备份提交")}</button><button className="btn btn-ghost" disabled={busy} onClick={()=>setPreview(null)}>{t("取消")}</button>
    </div>}
    <details><summary>{t("备份与恢复（")}{backups.length}）</summary>{backups.map(b=><div key={b.id} className="flex items-center gap-2 py-2"><span className="t-caption flex-1">{new Date(b.created_at).toLocaleString(lang==='en'?'en-US':'zh-CN')} · {b.snapshot_id.slice(0,12)}</span><button className="btn btn-ghost" disabled={!owner||busy||!configured} onClick={()=>void action(async(alive)=>{const restored=await storageCall('restore',{project_id:project,backup_id:b.id,side:'before'}) as Document;if(alive())setNotice(`已恢复为新文件 ${restored.name}，原文件未覆盖。`);})}>{t("恢复变更前的副本")}</button></div>)}</details>
    <div role="status" className="t-caption">{serverDisplayMessage(t,notice || '')}</div>
  </section>;
}
