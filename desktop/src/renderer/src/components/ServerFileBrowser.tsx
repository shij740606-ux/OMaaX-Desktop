import {serverError} from '../serverErrors';
import {useEffect,useRef,useState} from 'react';
import {Folder,File,Copy,ArrowLeft,ArrowRight,ArrowUp,Home} from 'lucide-react';
import type {SshDirectoryPage} from '@shared/types';
import {connectionRevision} from '../serverConnections';

const formatSize=(bytes:number)=>bytes>=1073741824?(bytes/1073741824).toFixed(2)+' GiB':bytes>=1048576?(bytes/1048576).toFixed(2)+' MiB':bytes>=1024?(bytes/1024).toFixed(1)+' KiB':bytes+' B';
export default function ServerFileBrowser({server,refreshToken=0}:{server:string;refreshToken?:number}){
 const saved=sessionStorage.getItem('omaax.server.files.path.'+server)||'';
 const [location,setLocation]=useState(saved),[draft,setDraft]=useState(saved),[page,setPage]=useState<SshDirectoryPage|null>(null);
 const [showHidden,setShowHidden]=useState(false),[filter,setFilter]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [history,setHistory]=useState([saved]),[position,setPosition]=useState(0);
 const token=useRef(0),current=useRef(''),revision=connectionRevision(server);current.current=server+'@'+revision;
 const load=async(path:string,append=false)=>{
  const expected=current.current,request=++token.current;setBusy(true);setError('');setNotice('');
  try{
   const result=await window.biodsh.sshHostFiles(server,{path,showHidden,offset:append?page?.nextOffset||0:0,limit:200});
   if(current.current!==expected||request!==token.current)return;
   setPage(old=>append&&old?.path===result.path?{...result,files:[...old.files,...result.files]}:result);setDraft(result.path);sessionStorage.setItem('omaax.server.files.path.'+server,result.path);
  }catch(reason){if(current.current===expected&&request===token.current)setError(serverError(reason,'files'));}
  finally{if(current.current===expected&&request===token.current)setBusy(false);}
 };
 useEffect(()=>{setPage(null);void load(location);return()=>{token.current++;};},[server,revision,location,showHidden,refreshToken]);
 const go=(path:string)=>{setFilter('');if(path===location){void load(path);return;}setHistory(old=>[...old.slice(0,position+1),path]);setPosition(position+1);setLocation(path);};
 const travel=(next:number)=>{if(next<0||next>=history.length)return;setPosition(next);setFilter('');setLocation(history[next]);};
 const copy=async(path:string)=>{try{await navigator.clipboard.writeText(path);setNotice('路径已复制：'+path);}catch{setDraft(path);setNotice('无法自动复制，请在路径框中选中并复制。');}};
 const crumbs=page?.path.split('/').filter(Boolean)||[];
 const rows=(page?.files||[]).filter(row=>row.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase())).slice().sort((a,b)=>Number(b.directory)-Number(a.directory)||a.name.localeCompare(b.name,'zh-CN',{numeric:true}));
 return <section className="card p-5 flex flex-col gap-4 min-w-0" aria-label="服务器文件浏览">
  <div><h2 className="t-headline">服务器文件</h2><p className="t-caption mt-1">浏览服务器上的文件和文件夹，查找并复制所需路径。无需先配置分析环境。</p></div>
  <div className="flex flex-wrap items-center gap-2"><button className="btn btn-ghost !px-2" aria-label="后退" disabled={busy||position===0} onClick={()=>travel(position-1)}><ArrowLeft size={16}/></button><button className="btn btn-ghost !px-2" aria-label="前进" disabled={busy||position===history.length-1} onClick={()=>travel(position+1)}><ArrowRight size={16}/></button><button className="btn btn-ghost" disabled={busy||!page?.parent} onClick={()=>page?.parent&&go(page.parent)}><ArrowUp size={14}/>上一级</button><button className="btn btn-ghost" disabled={busy} onClick={()=>go(page?.home||'~')}><Home size={14}/>家目录</button><button className="btn btn-ghost" disabled={busy} onClick={()=>void copy(page?.path||draft)}><Copy size={14}/>复制当前路径</button></div>
  <form className="flex gap-2 min-w-0" onSubmit={event=>{event.preventDefault();go(draft.trim()||'~');}}><input aria-label="服务器目录路径" className="field flex-1 min-w-0 font-mono" value={draft} onChange={event=>setDraft(event.target.value)} placeholder="输入绝对路径或 ~/，回车进入"/><button className="btn btn-primary" disabled={busy}>进入</button></form>
  <nav aria-label="当前服务器目录" className="flex flex-wrap items-center gap-1 t-caption"><button className="btn btn-ghost !px-2" disabled={busy} onClick={()=>go('/')}>/</button>{crumbs.map((part,index)=><button className="btn btn-ghost !px-2" disabled={busy} key={index} onClick={()=>go('/'+crumbs.slice(0,index+1).join('/'))}>{part} /</button>)}</nav>
  <div className="flex flex-wrap items-center gap-3"><input aria-label="筛选已加载文件" className="field flex-1 min-w-0" value={filter} onChange={event=>setFilter(event.target.value)} placeholder="按名称筛选已加载的文件"/><label className="flex gap-2 items-center t-caption"><input type="checkbox" checked={showHidden} disabled={busy} onChange={event=>setShowHidden(event.target.checked)}/>显示隐藏文件</label></div>
  {busy&&<p role="status">正在读取目录…</p>}{error&&<p role="alert" className="break-words" style={{color:'var(--red)'}}>{error}</p>}{notice&&<p role="status" className="t-caption break-all selectable">{notice}</p>}
  <div className="overflow-x-auto max-h-[520px] overflow-y-auto"><table className="w-full text-sm"><thead><tr className="text-left t-caption hairline-b"><th className="p-2">名称</th><th className="p-2 whitespace-nowrap">修改时间</th><th className="p-2">大小</th><th className="p-2">类型 / 权限</th><th className="p-2">路径</th></tr></thead><tbody>{rows.map(row=><tr className="hairline-b" key={row.path}><td className="p-2"><button className="flex items-center gap-2 text-left break-all" disabled={busy} onClick={()=>row.directory||row.link?go(row.path):void copy(row.path)}>{row.directory?<Folder size={18} style={{color:'var(--accent)',flexShrink:0}}/>:<File size={18} style={{flexShrink:0}}/>}{row.name}</button></td><td className="p-2 t-caption whitespace-nowrap">{row.modified?new Date(row.modified*1000).toLocaleString():'—'}</td><td className="p-2 t-caption whitespace-nowrap">{row.directory?'—':formatSize(row.size)}</td><td className="p-2 t-caption whitespace-nowrap">{row.directory?'文件夹':row.link?'符号链接':'文件'} · {row.permissions}</td><td className="p-2"><button className="btn btn-ghost !px-2" aria-label={'复制路径 '+row.name} onClick={()=>void copy(row.path)}><Copy size={14}/></button></td></tr>)}</tbody></table></div>
  {!busy&&page&&!rows.length&&<p className="t-caption">{filter?'已加载的条目中没有匹配项。':'此目录没有可显示的条目。'}</p>}
  <div className="flex flex-wrap items-center gap-3 t-caption"><span>已加载 {page?.files.length||0} 项{filter?' · 筛选结果 '+rows.length+' 项':''}</span>{page?.nextOffset!==null&&page?.nextOffset!==undefined&&<button className="btn btn-tint" disabled={busy} onClick={()=>void load(page.path,true)}>加载更多</button>}</div>
 </section>;
}
