import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

type Target = {id:string;label:string;image?:string};
type Phase = 'input'|'installing'|'checking'|'success'|'failed';
const InstallContext = createContext<(target:Target)=>void>(()=>{});

export function SudoInstallProvider({children}:{children:ReactNode}) {
 const [target,setTarget]=useState<Target|null>(null),[phase,setPhase]=useState<Phase>('input');
 const [password,setPassword]=useState(''),[message,setMessage]=useState('');
 const locked=useRef(false),targetRef=useRef<Target|null>(null);
 const busy=phase==='installing'||phase==='checking';
 locked.current=!!target&&phase!=='input';targetRef.current=target;
 useEffect(()=>{
  const unload=(e:BeforeUnloadEvent)=>{if(locked.current){e.preventDefault();e.returnValue='';}};
  window.addEventListener('beforeunload',unload);
  let disposed=false,unlisten:(()=>void)|undefined;
  if('__TAURI_INTERNALS__' in window) void import('@tauri-apps/api/window').then(async({getCurrentWindow})=>{
   const stop=await getCurrentWindow().onCloseRequested(event=>{if(locked.current)event.preventDefault();});
   if(disposed)stop();else unlisten=stop;
  }).catch(()=>{});
  return()=>{disposed=true;unlisten?.();window.removeEventListener('beforeunload',unload);};
 },[]);
 const open=(next:Target)=>{if(targetRef.current)return;targetRef.current=next;setTarget(next);setPhase('input');setPassword('');setMessage('');};
 const close=()=>{if(busy)return;locked.current=false;targetRef.current=null;setTarget(null);setPassword('');setMessage('');};
 const submit=async()=>{
  if(!target||busy||!password||locked.current)return;
  const selected={...target};const secret=password;locked.current=true;setPassword('');setPhase('installing');setMessage('正在安装系统组件，请保持网络连接…');
  let installed=false;
  try{
   await window.biodsh.personalCompute(selected.id,'install_environment',{password:secret});installed=true;
   setPhase('checking');setMessage('安装完成，正在检查是否可以开始分析…');
   await window.biodsh.personalCompute(selected.id,'bootstrap',selected.image?{image:selected.image}:{});
   setPhase('success');setMessage('安装与检查完成，请返回分析任务查看环境状态。');
   window.dispatchEvent(new CustomEvent('biodsh-install-finished',{detail:{id:selected.id}}));
  }catch(reason){setPhase('failed');setMessage(`${installed?'系统组件已安装，但后续环境配置未完成。\n':''}${String(reason)}`);}
 };
 return <InstallContext.Provider value={open}>{children}{target&&<section role="dialog" aria-label="服务器环境安装" className="fixed z-[100] right-4 bottom-4 card p-5 flex flex-col gap-4 overflow-y-auto no-drag" style={{width:'min(600px, calc(100vw - 32px))',maxHeight:'calc(100vh - 32px)',boxShadow:'0 12px 48px rgba(0,0,0,.22)',border:'1px solid var(--border)'}}>
  <div className="t-title2">{phase==='success'?'安装完成':phase==='failed'?'安装未完成':'服务器环境安装'}</div>
  <div className="text-sm break-words">目标：{target.label}</div>
  <div className="t-caption">切换页面不会丢失此窗口。安装期间不能关闭，完成后请确认结果。</div>
  {phase==='input'?<form className="flex flex-col gap-3" onSubmit={e=>{e.preventDefault();void submit();}}>
   <div className="text-sm">将在服务器上安装分析必需的软件，需要管理员权限。</div>
   <label className="text-sm">服务器账号密码（需有 sudo 权限）<input autoFocus className="field w-full mt-2" type="password" autoComplete="off" required maxLength={4096} value={password} onChange={e=>setPassword(e.target.value)}/></label>
   <div className="t-caption">密码仅用于本次安装，不会保存。</div><details className="t-caption"><summary>安装内容</summary>Python 3、Podman、uidmap、slirp4netns、fuse-overlayfs 及依赖。支持 Ubuntu / Debian 等 apt 系统。</details>
   <div className="flex gap-2"><button className="btn btn-primary" disabled={!password}>授权安装并继续配置</button><button type="button" className="btn btn-ghost" onClick={close}>取消</button></div>
  </form>:<>
   <div role={phase==='failed'?'alert':'status'} className="rounded-xl p-3 text-sm whitespace-pre-wrap break-words" style={{background:'var(--fill)',color:phase==='failed'?'var(--red)':phase==='success'?'var(--green)':undefined}}>{phase==='failed'?'安装未完成，请查看原因后重试。':message}</div>{phase==='failed'&&<details className="t-caption break-words"><summary>查看原因</summary>{message}</details>}
   {busy?<button className="btn btn-ghost self-start" disabled>正在配置，请等待结果…</button>:<button autoFocus className="btn btn-primary self-start" onClick={close}>确认结果并关闭</button>}
  </>}
 </section>}</InstallContext.Provider>;
}
export default function SudoInstall(target:Target) {
 const open=useContext(InstallContext);
 return <section className="card p-4 flex flex-col gap-3"><div className="t-headline">安装分析所需环境</div><div className="t-caption">服务器：{target.label}。需要此账号的管理员权限，安装后才能运行分析。</div><button className="btn btn-tint self-start" onClick={()=>open(target)}>授权安装</button></section>;
}
