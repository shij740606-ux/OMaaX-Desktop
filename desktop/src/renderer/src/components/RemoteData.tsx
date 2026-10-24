import {serverDisplayMessage} from '../serverDisplay';
import {useT} from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Folder, RefreshCw } from 'lucide-react';
import { useApp } from '../store';

export interface SharedRoot {
  id: string; name: string; enabled: boolean; project_ids?: string[];
  bindings: {node_id:string; node_name:string; local_key?:string; status:string; checked_at:string|null}[];
}
interface Entry { name:string; kind:string; size_bytes:number; modified_ns:number }
interface Page { status:string; entries?:Entry[]; next_cursor?:string }
const statusText: Record<string,string> = {
  pending:'等待服务器响应', running:'正在读取目录', expired:'查询超时，请重试',
  unavailable:'目录不可用，请管理员检查挂载或目录配置', timeout:'目录读取超时，请稍后重试',
  changed:'目录已发生变化，请刷新后重新浏览', too_large:'目录文件过多，请管理员拆分子目录',
  denied:'目录未获授权或不允许访问', offline:'服务器离线', unverified:'尚未验证', ready:'可访问',
};
export const storageCall = (action:string, body:unknown = {}) => window.biodsh.platformStorage(action, body);

export default function RemoteData() {
 const {t,lang}=useT();
  const { platform, settings } = useApp();
  if (!platform.authenticated) return <div className="card p-4">{t("远程数据：请先在设置中登录平台。")}</div>;
  if (!settings?.currentProjectId) return <div className="card p-4">{t("远程数据：请先选择项目。")}</div>;
  return <Browser key={`${platform.user?.id}:${settings.currentProjectId}`} projectId={settings.currentProjectId} />;
}

function Browser({projectId}:{projectId:string}) {
 const {t,lang}=useT();
  const [roots,setRoots] = useState<SharedRoot[]>([]);
  const [rootId,setRootId] = useState('');
  const [path,setPath] = useState('');
  const [page,setPage] = useState<Page|null>(null);
  const [notice,setNotice] = useState('');
  const [busy,setBusy] = useState(false);
  const generation = useRef(0);
  const reloadRoots = async () => {
    const version=++generation.current;
    setBusy(true); setPage(null); setPath(''); setNotice('');
    try {
      const rows=await storageCall('list',{project_id:projectId}) as SharedRoot[];
      if(version!==generation.current)return;
      setRoots(rows); setRootId(previous=>rows.some(r=>r.id===previous)?previous:rows[0]?.id || '');
    } catch(error) {if(version===generation.current){setRoots([]);setRootId('');setNotice(String(error).replace(/^Error:\s*/,''));}}
    finally {if(version===generation.current)setBusy(false);}
  };
  useEffect(() => {
    let live = true;
    const version=++generation.current;
    void storageCall('list',{project_id:projectId}).then(value => {
      if (live && version===generation.current) {const rows = value as SharedRoot[]; setRoots(rows); setRootId(rows[0]?.id || '');}
    }).catch(error => live && version===generation.current && setNotice(String(error).replace(/^Error:\s*/,'')));
    return () => {live=false; generation.current++;};
  },[projectId]);
  const browse = async (directory:string, cursor='') => {
    if (!rootId || busy) return;
    const version = ++generation.current;
    const live = () => version === generation.current;
    setBusy(true); setPage(null); setNotice('正在查询服务器…');
    try {
      const request = await storageCall('browse',{project_id:projectId,root_id:rootId,path:directory,cursor}) as {id:string};
      for (let i=0; i<60 && live(); i++) {
        const response = await storageCall('result',{project_id:projectId,request_id:request.id}) as {status:string;result?:Page};
        if (!live()) return;
        if (response.status==='complete' && response.result) {
          setPath(directory); setPage(response.result);
          setNotice(response.result.status==='ready' ? '只读浏览 · 文件仍保存在服务器上' : statusText[response.result.status] || '查询失败');
          return;
        }
        if (!['pending','running'].includes(response.status)) throw new Error(statusText[response.status] || '查询失败');
        await new Promise(resolve => setTimeout(resolve,1000));
      }
      if (live()) setNotice('服务器响应超时，请检查服务器是否在线后重试。');
    } catch(error) {if (live()) setNotice(String(error).replace(/^Error:\s*/,''));}
    finally {if (live()) setBusy(false);}
  };
  return <section className="card p-4 flex flex-col gap-3" aria-label={t("远程数据")}>
    <div className="flex items-center justify-between"><div className="t-headline">{t("远程数据")}</div><button className="btn btn-ghost" disabled={busy} onClick={()=>void reloadRoots()}>{t("刷新目录列表")}</button></div>
    <div className="t-caption">{t("浏览当前项目获授权的共享目录。此处不会下载、移动或删除原文件。")}</div>
    <div className="flex gap-2">
      <select aria-label={t("共享目录")} className="field flex-1" value={rootId} disabled={busy} onChange={e => {generation.current++; setRootId(e.target.value); setPath(''); setPage(null); setNotice('');}}>
        {!roots.length && <option value="">{t("暂无获授权的目录")}</option>}
        {roots.map(root => <option key={root.id} value={root.id}>{root.name}</option>)}
      </select>
      <button className="btn btn-primary" disabled={!rootId || busy} onClick={() => void browse(path)}><RefreshCw size={14}/>{busy?t("查询中…"):t("查询 / 刷新")}</button>
    </div>
    <div className="t-caption">{roots.find(r=>r.id===rootId)?.bindings.map(b=>`${b.node_name}：${t(statusText[b.status] || b.status)}${b.checked_at ? `（${new Date(b.checked_at).toLocaleString(lang==='en'?'en-US':'zh-CN')}）` : ''}`).join(' · ')}</div>
    <div className="flex items-center gap-2"><button className="btn btn-ghost" disabled={!path || busy} onClick={() => void browse(path.split('/').slice(0,-1).join('/'))}>{t("上一级")}</button><span className="t-caption break-all">/{path}</span></div>
    <div role="status" className="t-caption">{serverDisplayMessage(t,notice || '')}</div>
    {page?.status==='ready' && <>
      {!page.entries?.length && <div className="t-caption">{t("此目录为空")}</div>}
      {page.entries?.map(entry => <div key={entry.name} className="flex gap-3 items-center hairline-b py-2">
        {entry.kind==='directory' ? <button className="btn btn-ghost min-w-0 flex-1" style={{justifyContent:'flex-start'}} disabled={busy} onClick={() => void browse([path,entry.name].filter(Boolean).join('/'))}><Folder size={15}/><span className="truncate">{entry.name}</span></button> : <span className="t-body min-w-0 flex-1 truncate px-3" title={entry.name}>{entry.name}</span>}
        <span className="t-caption">{entry.kind==='file'?t('{n} 字节',{n:entry.size_bytes.toLocaleString(lang==='en'?'en-US':'zh-CN')}):entry.kind==='symlink'?t("链接（不可进入）"):entry.kind==='directory'?t("文件夹"):t("其他")}</span>
        <span className="t-caption">{new Date(entry.modified_ns/1e6).toLocaleString(lang==='en'?'en-US':'zh-CN')}</span>
      </div>)}
      {page.next_cursor && <button className="btn btn-ghost" disabled={busy} onClick={() => void browse(path,page.next_cursor)}>{t("下一页")}</button>}
    </>}
  </section>;
}
