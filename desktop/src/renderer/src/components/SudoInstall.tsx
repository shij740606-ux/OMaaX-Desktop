import {serverError} from '../serverErrors';
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import {connectionState,prepareServer} from '../serverConnections';

type Target = {id:string;label:string;image?:string};
type Phase = 'input'|'installing'|'checking'|'success'|'failed';
const InstallContext = createContext<(target:Target)=>void>(()=>{});

export function SudoInstallProvider({children}:{children:ReactNode}) {
 const [target,setTarget]=useState<Target|null>(null),[phase,setPhase]=useState<Phase>('input');
 const [message,setMessage]=useState('');
 const locked=useRef(false),targetRef=useRef<Target|null>(null);
 const busy=phase==='installing'||phase==='checking';
 locked.current=!!target&&busy;targetRef.current=target;
 useEffect(()=>{
  const unload=(e:BeforeUnloadEvent)=>{if(locked.current){e.preventDefault();e.returnValue='';}};
  window.addEventListener('beforeunload',unload);
  let disposed=false,unlisten:(()=>void)|undefined;
  if('__TAURI_INTERNALS__' in window) void import('@tauri-apps/api/window').then(async({getCurrentWindow})=>{
   const stop=await getCurrentWindow().onCloseRequested(event=>{if(locked.current&&!window.confirm('服务器组件仍在安装。关闭软件可能中断本次连接；下次连接时可重新检查并继续配置。确定现在关闭？'))event.preventDefault();});
   if(disposed)stop();else unlisten=stop;
  }).catch(()=>{});
  return()=>{disposed=true;unlisten?.();window.removeEventListener('beforeunload',unload);};
 },[]);
 const open=(next:Target)=>{if(targetRef.current)return;targetRef.current=next;setTarget(next);setPhase('input');setMessage('');};
 const close=()=>{if(busy)return;locked.current=false;targetRef.current=null;setTarget(null);setMessage('');};
 const submit=async()=>{
  if(!target||busy||locked.current)return;
  const selected={...target};locked.current=true;setPhase('installing');setMessage('正在准备用户环境，请保持网络连接…');
  try{
   await prepareServer(selected.id,true,selected.image);
   const setup=connectionState(selected.id)?.setup;
   if(setup&&['failed','interrupted'].includes(setup.state))throw Error(setup.errorDetail||setup.detail);
   setPhase('success');setMessage('环境准备已提交。服务器页面会继续显示准备结果，可关闭此窗口。');
  }catch(reason){setPhase('failed');setMessage(serverError(reason,'environment'));}

 };
 return <InstallContext.Provider value={open}>{children}{target&&<section role="dialog" aria-label="服务器环境安装" className="fixed z-[100] right-4 bottom-4 card p-5 flex flex-col gap-4 overflow-y-auto no-drag" style={{width:'min(600px, calc(100vw - 32px))',maxHeight:'calc(100vh - 32px)',boxShadow:'0 12px 48px rgba(0,0,0,.22)',border:'1px solid var(--border)'}}>
  <div className="t-title2">{phase==='success'?'已提交配置':phase==='failed'?'安装未完成':'服务器环境安装'}</div>
  <div className="text-sm break-words">目标：{target.label}</div>
  <div className="t-caption">切换页面不会丢失进度。安装失败后可以关闭并重试；已有组件会复用，不会重复安装全部工具。</div>
  {phase==='input'?<form className="flex flex-col gap-3" onSubmit={e=>{e.preventDefault();void submit();}}>
   <div className="text-sm">在当前服务器账号的目录中准备环境，无需管理员密码。已有软件会优先复用。</div>
   <div className="flex gap-2"><button className="btn btn-primary">开始配置</button><button type="button" className="btn btn-ghost" onClick={close}>取消</button></div>
  </form>:<>
   <div role={phase==='failed'?'alert':'status'} className="rounded-xl p-3 text-sm whitespace-pre-wrap break-words" style={{background:'var(--fill)',color:phase==='failed'?'var(--red)':phase==='success'?'var(--green)':undefined}}>{phase==='failed'?'安装未完成，请查看原因后重试。':message}</div>{phase==='failed'&&<details className="t-caption break-words"><summary>查看原因</summary>{message}</details>}
   {busy?<button className="btn btn-ghost self-start" disabled>正在配置，请等待结果…</button>:<button autoFocus className="btn btn-primary self-start" onClick={close}>确认结果并关闭</button>}
  </>}
 </section>}</InstallContext.Provider>;
}
export default function SudoInstall(target:Target) {
 const open=useContext(InstallContext);
 return <section className="card p-4 flex flex-col gap-3"><div className="t-headline">安装分析所需环境</div><div className="t-caption">服务器：{target.label}。使用普通账号权限配置分析环境。</div><button className="btn btn-tint self-start" onClick={()=>open(target)}>准备用户环境</button></section>;
}
