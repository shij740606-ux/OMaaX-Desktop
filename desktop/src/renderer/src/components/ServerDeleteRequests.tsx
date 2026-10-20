import {serverError} from '../serverErrors';
import {useEffect,useRef,useState} from 'react';
type Request={id:string;digest:string;policy?:string;paths:string[];snapshot:{bytes:number;entries:number};warnings:string[];context?:{sessionId?:string;projectName:string;sessionName:string}};
function Notice({row,shown,cancel,disabled,automatic}:{row:Request;shown:()=>void;cancel:()=>void;disabled:boolean;automatic:boolean}){
 const element=useRef<HTMLElement>(null),callback=useRef(shown);callback.current=shown;
 useEffect(()=>{
  if(!automatic||disabled||!element.current||typeof IntersectionObserver==='undefined')return;
  let visible=false,timer:ReturnType<typeof setTimeout>|undefined;
  const reset=()=>{if(timer)clearTimeout(timer);timer=undefined;if(visible&&document.visibilityState==='visible')timer=setTimeout(()=>callback.current(),5000);};
  const observer=new IntersectionObserver(([entry])=>{visible=entry.isIntersecting&&entry.intersectionRatio>=0.5;reset();},{threshold:[0,0.5,1]});
  observer.observe(element.current);document.addEventListener('visibilitychange',reset);
  return()=>{observer.disconnect();if(timer)clearTimeout(timer);document.removeEventListener('visibilitychange',reset);};
 },[row.id,row.digest,disabled,automatic]);
 return <article ref={element} className="py-3 hairline-b"><strong>{row.context?.projectName} · {row.context?.sessionName}</strong><p>以下文件将移入回收站，保留恢复副本。{automatic?'提醒展示后自动处理，可以取消。':'请回到发起请求的对话查看提醒；此处不会自动执行。'}</p><p role="alert" style={{color:'var(--orange)'}}>{row.warnings.join('；')}</p><p>{row.snapshot.entries} 个文件或目录 · {row.snapshot.bytes.toLocaleString()} 字节（{(row.snapshot.bytes/1048576).toFixed(2)} MiB）</p><ul>{row.paths.map(p=><li key={p} className="selectable break-all">{p}</li>)}</ul><button className="btn btn-ghost" disabled={disabled} onClick={cancel}>取消删除</button></article>;
}
export default function ServerDeleteRequests({server,sessionId}:{server:string;sessionId?:string}){
 const [rows,setRows]=useState<Request[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [completed,setCompleted]=useState<Request[]>([]);const sending=useRef(new Set<string>());
 useEffect(()=>{setRows([]);setCompleted([]);setError('');sending.current.clear();},[server,sessionId]);
 const call=<T,>(action:string,body:unknown={})=>window.biodsh.personalCompute(server,action,body) as Promise<T>;
 useEffect(()=>{let active=true,running=false;const load=async()=>{if(running)return;running=true;try{const value=await call<Request[]>('server_delete_requests');if(active){setRows(value);setError('');}}catch(e){if(active)setError(serverError(e,'files'));}finally{running=false;}};void load();const timer=setInterval(()=>void load(),10000);return()=>{active=false;clearInterval(timer);};},[server]);
 const act=async(row:Request,confirm:boolean)=>{setBusy(true);setError('');try{await call(confirm?'server_delete_confirm':'server_delete_reject',{id:row.id,digest:row.digest});setRows(await call<Request[]>('server_delete_requests'));window.dispatchEvent(new CustomEvent('omaax-files-changed',{detail:{server}}));}catch(e){setError(serverError(e,'files'));}finally{setBusy(false);}};
 const notified=async(row:Request)=>{if(sending.current.has(row.id))return;sending.current.add(row.id);setBusy(true);try{await call('server_delete_notified',{id:row.id,digest:row.digest});setCompleted(old=>[...old,row]);setRows(old=>old.filter(item=>item.id!==row.id));window.dispatchEvent(new CustomEvent('omaax-files-changed',{detail:{server}}));}catch(e){setError(serverError(e,'files'));}finally{setBusy(false);}};
 const visible=rows.filter(row=>!sessionId||row.context?.sessionId===sessionId);
 const done=completed.filter(row=>!sessionId||row.context?.sessionId===sessionId);
 if(!visible.length&&!error&&!done.length)return null;
 return <section className="card p-5 flex flex-col gap-3" aria-label="删除提醒"><h2>删除提醒</h2>{error&&<p role="alert">{error}</p>}{done.map(row=><p key={row.id}>已移入回收站，可恢复：{row.paths.join('、')}（{row.snapshot.bytes.toLocaleString()} 字节）</p>)}{visible.map(row=>row.policy==='notice-v1'?<Notice key={row.id} row={row} automatic={!!sessionId} disabled={busy||!!error} shown={()=>void notified(row)} cancel={()=>void act(row,false)}/>:<article key={row.id} className="py-3 hairline-b"><strong>{row.context?.projectName} · {row.context?.sessionName}</strong><p role="alert" style={{color:'var(--orange)'}}>{row.warnings.join('；')}</p><p>{row.snapshot.entries} 个文件或目录 · {row.snapshot.bytes.toLocaleString()} 字节（{(row.snapshot.bytes/1048576).toFixed(2)} MiB）</p><details open><summary>实际处理范围 · {row.paths.length} 项</summary><ul>{row.paths.map(p=><li key={p} className="selectable break-all">{p}</li>)}</ul></details><div className="flex gap-2 mt-3"><button className="btn btn-tint" disabled={busy} onClick={()=>void act(row,true)}>已知悉范围，确认移入回收站</button><button className="btn btn-ghost" disabled={busy} onClick={()=>void act(row,false)}>取消删除</button></div></article>)}</section>;
}
