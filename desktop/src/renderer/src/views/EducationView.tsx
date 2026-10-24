import {useEffect,useRef,useState} from 'react';
import {useApp} from '../store';
import {useT} from '../i18n';
export default function EducationView({active}:{active:boolean}){
 const {t,lang}=useT();
 const latestLang=useRef(lang);latestLang.current=lang;
 const {setTab,settings}=useApp();const frame=useRef<HTMLIFrameElement>(null);
 const [url,setUrl]=useState(''),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 const [requested,setRequested]=useState(active);
 useEffect(()=>{if(active)setRequested(true);},[active]);
 // Navigation only hides the workspace; it must not cancel its pending startup.
 useEffect(()=>{if(!requested)return;let alive=true;setError('');
  window.biodsh.educationStart().then(value=>{if(alive){const target=new URL(value);target.searchParams.set('language',latestLang.current);setUrl(target.href);}}).catch(e=>{if(alive)setError(String(e));});
  return()=>{alive=false;};
 },[requested,attempt]);
 const reload=()=>{setUrl('');setError('');setAttempt(x=>x+1);};
 const theme=()=>settings?.theme==='dark'||(settings?.theme==='system'&&matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';
 const syncPreferences=()=>{if(url){const origin=new URL(url).origin;frame.current?.contentWindow?.postMessage({type:'omaax-theme',theme:theme()},origin);frame.current?.contentWindow?.postMessage({type:'omaax-locale',language:lang},origin);}};
 useEffect(syncPreferences,[settings?.theme,url,lang]);
 useEffect(()=>{if(!url)return;let alive=true;const origin=new URL(url).origin;
  const receive=async(event:MessageEvent)=>{
   if(event.source!==frame.current?.contentWindow||event.origin!==origin)return;
   const data=event.data;
   if(data?.type==='omaax-preferences-ready')syncPreferences();
   if(data?.type==='omaax-back')setTab('chat');
   if(data?.type==='omaax-external'&&typeof data.url==='string'&&/^https?:\/\//.test(data.url))void window.biodsh.openExternal(data.url);
   if(data?.type==='omaax-delete'&&typeof data.path==='string'&&typeof data.requestId==='string'){
    try{const value=await window.biodsh.educationDelete(data.path);if(alive)frame.current?.contentWindow?.postMessage({type:'omaax-delete-result',requestId:data.requestId,value},origin);}
    catch(e){if(alive)frame.current?.contentWindow?.postMessage({type:'omaax-delete-result',requestId:data.requestId,error:String(e)},origin);}
   }
  };window.addEventListener('message',receive);return()=>{alive=false;window.removeEventListener('message',receive);};
 },[url,setTab,lang,settings?.theme]);
 return <div className="h-full flex flex-col" style={{display:active?'flex':'none'}}>
  <header className="drag h-[52px] shrink-0 flex items-center pl-5 hairline-b"><span className="t-title2">{t('教育科研')}</span>{url&&<button className="no-drag btn btn-ghost ml-4" title={t('页面异常时重新加载；未提交的内容将清空')} onClick={reload}>{t('重新加载')}</button>}</header>
  {error?<div className="p-8"><p role="alert">{t(error)}</p><button className="btn btn-primary mt-4" onClick={reload}>{t('重试')}</button></div>:url?<iframe ref={frame} title={t('教育科研工作台')} className="flex-1 w-full border-0" src={url} onLoad={syncPreferences} sandbox="allow-scripts allow-same-origin allow-downloads allow-modals"/>:<p className="p-8" role="status">{t('正在准备教育科研，准备好后会自动显示。可以先使用其他功能。')}</p>}
 </div>;
}
