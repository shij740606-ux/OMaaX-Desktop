import {serverError} from '../serverErrors';
import {useEffect,useState} from 'react';
import type {AnalysisContext} from '../views/analysisContext';
type Request={id:string;path:string;digest:string;context:AnalysisContext;previous:{size:number};source?:string;preview?:string;proposedBytes?:number};
export default function ServerWriteRequests({server}:{server:string}){
 const [rows,setRows]=useState<Request[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const call=<T,>(action:string,body:unknown={})=>window.biodsh.personalCompute(server,action,body) as Promise<T>;
 const load=async()=>setRows(await call<Request[]>('server_write_requests'));
 useEffect(()=>{let active=true;const refresh=()=>void call<Request[]>('server_write_requests').then(rows=>{if(active)setRows(rows);}).catch(e=>{if(active)setError(serverError(e,'files'));});refresh();const timer=setInterval(refresh,5000);return()=>{active=false;clearInterval(timer);};},[server]);
 const run=async(action:string,row:Request)=>{setBusy(true);setError('');try{const result=await call<{backup?:string}>(action,action==='server_write_confirm'?{id:row.id,digest:row.digest}:{id:row.id});setNotice(result.backup?'已写入，覆盖前的原文件备份已保留在本次对话的工作文件夹。':'请求已取消，原文件保持不变。');await load();}catch(e){setError(serverError(e,'files'));}finally{setBusy(false);}};
 if(!rows.length&&!error&&!notice)return null;
 return <section className="card p-5 flex flex-col gap-3"><h2>已有文件写入请求</h2><p>这里显示旧版本遗留的待处理请求。新任务会直接保存，覆盖前保留备份；已登记的原始数据仍受保护。</p>{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{rows.map(row=><article key={row.id} className="py-3 hairline-b"><strong>{row.context.projectName} · {row.context.sessionName}</strong><p className="selectable break-all mt-2">目标文件：{row.path}</p><p className="t-caption">原文件大小：{(row.previous.size/1024).toFixed(1)} KB</p>{row.source&&<p className="t-caption selectable break-all">拟复制的文件：{row.source}</p>}{row.preview!==undefined&&<details><summary>查看拟写入内容</summary><pre className="t-caption selectable whitespace-pre-wrap break-words max-h-60 overflow-auto">{row.preview}</pre>{(row.proposedBytes||0)>16384&&<p className="t-caption">此处显示内容开头。</p>}</details>}<div className="flex gap-2 mt-2"><button className="btn btn-tint" disabled={busy} onClick={()=>void run('server_write_confirm',row)}>明确授权覆盖此文件</button><button className="btn btn-ghost" disabled={busy} onClick={()=>void run('server_write_reject',row)}>拒绝覆盖</button></div></article>)}</section>;
}
