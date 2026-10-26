import {useT} from '../i18n';
import {createContext,useContext,useEffect,useRef,useState} from 'react';
import Markdown,{type Components} from 'react-markdown';
import remarkGfm from 'remark-gfm';

let activeImages=0;
const imageWaiters:Array<()=>void>=[];
const ImageSession=createContext<string|null>(null);
async function loadImage(src:string,sessionId:string|null,refresh:boolean,live:()=>boolean){
 // The native request owns persistence after the view closes and bounds downloads.
 if(sessionId)return window.biodsh.cloudQa('image',{url:src,sessionId,...(refresh?{refresh:true}:{})});
 if(activeImages>=3)await new Promise<void>(resolve=>imageWaiters.push(resolve));else activeImages++;
 try {if(!live())throw Error('cancelled');return await window.biodsh.cloudQa('image',{url:src,...(sessionId?{sessionId}:{})});}
 finally {const next=imageWaiters.shift();if(next)next();else activeImages--;}
}
function imageError(value:unknown){
 const message=String(value);
 if(message.includes('UNAUTHORIZED'))return '登录已过期，请重新登录后查看图片。';
 if(message.includes('IMAGE_DENIED'))return '此图片尚未保存在本机，原链接已失效或无权访问。请重新查询以获取图片。';
 if(message.includes('IMAGE_MISSING'))return '文献图片暂不可用，请稍后重新加载。';
 return '图片暂时无法加载，请稍后重试。';
}
export async function copyKnowledgeImage(data:string){
 const img=new Image();img.src=data;await img.decode();
 if(!img.naturalWidth||!img.naturalHeight||img.naturalWidth*img.naturalHeight>40_000_000)throw Error('图片过大，请保存图片');
 const canvas=document.createElement('canvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;
 const context=canvas.getContext('2d');if(!context)throw Error('无法复制图片');context.drawImage(img,0,0);
 const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('图片转换失败')),'image/png'));
 await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
}
export function KnowledgeImage({src,alt}:{src?:string;alt?:string}){
 const {t,lang}=useT();
 const sessionId=useContext(ImageSession);
 const [image,setImage]=useState(''),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 const [retentionFailed,setRetentionFailed]=useState(false);
 const [actionBusy,setActionBusy]=useState(false),[notice,setNotice]=useState('');
 async function act(save:boolean){setActionBusy(true);setNotice('');try{if(save){const result=await window.biodsh.cloudQa('save_image',{data:image,lang}) as {saved:boolean};setNotice(result.saved?'图片已保存':'');}else{await copyKnowledgeImage(image);setNotice('图片已复制');}}catch{setNotice(save?'保存失败，请重试':'复制失败，请尝试保存图片');}finally{setActionBusy(false);}}
 const request=useRef<{key:string;promise:Promise<unknown>}|null>(null);
 const invalidImage=useRef(''),sourceKey=JSON.stringify([sessionId,src]);
 const current=useRef('');
 useEffect(()=>{let alive=true;setImage('');setError('');setNotice('');setRetentionFailed(false);
  if(!src){setError('图片地址无效');return;}
  const key=JSON.stringify([sessionId,src,attempt]);
  current.current=key;
  if(request.current?.key!==key)request.current={key,promise:loadImage(src,sessionId,invalidImage.current===sourceKey,()=>current.current===key)};
  void request.current.promise.then(value=>{
   const data=(value as {src?:unknown})?.src;
   if(typeof data!=='string'||!/^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/.test(data))throw Error('图片加载失败');
   if(alive){setImage(data);setRetentionFailed(!!sessionId&&(value as {persisted?:boolean}).persisted===false);}
  }).catch(value=>{if(alive)setError(imageError(value));});
  return()=>{alive=false;current.current='';};
 },[src,sessionId,attempt]);
 return <span className="block my-3">{image?<><img src={image} alt={alt||t('文献图片')} className="max-w-full h-auto rounded" onLoad={()=>{invalidImage.current='';}} onError={()=>{invalidImage.current=sourceKey;setImage('');setError('图片无法显示，请重试');}}/><span className="flex gap-2 mt-2"><button type="button" className="btn btn-ghost" disabled={actionBusy} onClick={()=>void act(false)}>{t("复制图片")}</button><button type="button" className="btn btn-ghost" disabled={actionBusy} onClick={()=>void act(true)}>{t("保存图片")}</button></span>{retentionFailed&&<span role="status">{t('图片已显示，但未能随会话保存到本机。请重试保留或另存图片。')}<button type="button" className="btn ml-2" onClick={()=>setAttempt(v=>v+1)}>{t('重试保留图片')}</button></span>}{notice&&<span role="status">{t(notice)}</span>}</>:error?<span role="status">{t(error.replace(/^Error:\s*/,''))}<button type="button" className="btn ml-2" onClick={()=>setAttempt(v=>v+1)}>{t("重新加载图片")}</button></span>:<span role="status">{t("正在加载文献图片…")}</span>}</span>;
}
const components:Components={
  img:({src,alt})=><KnowledgeImage src={src} alt={alt}/>,
  a:function KnowledgeLink({href,children}){const {t}=useT();let safe=false;try{const u=new URL(href||'');safe=['https:','http:'].includes(u.protocol)&&!u.username&&!u.password;}catch{}
   return safe?<a href={href} className="text-blue-600 underline" onClick={e=>{e.preventDefault();void window.biodsh.openExternal(href!).catch(()=>window.alert(t('链接无法打开，请稍后重试')));}}>{children}</a>:<span>{children}</span>;},
  h1:({children})=><h2 className="text-xl font-semibold my-4">{children}</h2>,
  h2:({children})=><h3 className="text-lg font-semibold my-3">{children}</h3>,
  h3:({children})=><h4 className="font-semibold my-3">{children}</h4>,
  p:({children})=><p className="my-3">{children}</p>,
  ul:({children})=><ul className="list-disc pl-6 my-2">{children}</ul>,
  ol:({children})=><ol className="list-decimal pl-6 my-2">{children}</ol>,
  table:({children})=><div className="overflow-x-auto"><table className="border-collapse my-3">{children}</table></div>,
  th:({children})=><th className="border p-2 text-left">{children}</th>,td:({children})=><td className="border p-2">{children}</td>,
  pre:({children})=><pre className="overflow-x-auto whitespace-pre bg-gray-100 rounded p-3">{children}</pre>,
};
export default function KnowledgeMarkdown({text,sessionId=null}:{text:string;sessionId?:string|null}){

 return <ImageSession.Provider value={sessionId}><div className="mt-2 break-words leading-relaxed"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={components}>{text}</Markdown></div></ImageSession.Provider>;
}
