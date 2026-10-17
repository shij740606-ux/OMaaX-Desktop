import {useEffect,useRef,useState} from 'react';
import {useApp} from '../store';
export default function EducationView({active}:{active:boolean}){
 const {setTab,settings}=useApp();const frame=useRef<HTMLIFrameElement>(null);
 const [url,setUrl]=useState(''),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 const opened=useRef(false);
 useEffect(()=>{if(!active||opened.current)return;opened.current=true;let alive=true;setError('');
  window.biodsh.educationStart().then(value=>{if(alive)setUrl(value);}).catch(e=>{if(alive){opened.current=false;setError(String(e));}});
  return()=>{alive=false;opened.current=false;};
 },[active,attempt]);
 const theme=()=>settings?.theme==='dark'||(settings?.theme==='system'&&matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';
 const syncTheme=()=>{if(url)frame.current?.contentWindow?.postMessage({type:'omaax-theme',theme:theme()},new URL(url).origin);};
 useEffect(syncTheme,[settings?.theme,url]);
 useEffect(()=>{if(!url)return;let alive=true;const origin=new URL(url).origin;
  const receive=async(event:MessageEvent)=>{
   if(event.source!==frame.current?.contentWindow||event.origin!==origin)return;
   const data=event.data;
   if(data?.type==='omaax-back')setTab('chat');
   if(data?.type==='omaax-external'&&typeof data.url==='string'&&/^https?:\/\//.test(data.url))void window.biodsh.openExternal(data.url);
   if(data?.type==='omaax-delete'&&typeof data.path==='string'&&typeof data.requestId==='string'){
    try{const value=await window.biodsh.educationDelete(data.path);if(alive)frame.current?.contentWindow?.postMessage({type:'omaax-delete-result',requestId:data.requestId,value},origin);}
    catch(e){if(alive)frame.current?.contentWindow?.postMessage({type:'omaax-delete-result',requestId:data.requestId,error:String(e)},origin);}
   }
  };window.addEventListener('message',receive);return()=>{alive=false;window.removeEventListener('message',receive);};
 },[url,setTab]);
 return <div className="h-full flex flex-col" style={{display:active?'flex':'none'}}>
  <header className="drag h-[52px] shrink-0 flex items-center pl-5 hairline-b"><span className="t-title2">教育科研</span></header>
  {error?<div className="p-8"><p role="alert">{error}</p><button className="btn btn-primary mt-4" onClick={()=>{opened.current=false;setUrl('');setAttempt(x=>x+1);}}>重新连接</button></div>:url?<iframe ref={frame} title="教育科研工作台" className="flex-1 w-full border-0" src={url} onLoad={syncTheme} sandbox="allow-scripts allow-same-origin allow-downloads allow-modals"/>:<p className="p-8">正在打开教育科研…</p>}
 </div>;
}
