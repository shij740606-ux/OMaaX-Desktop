import {useEffect,useRef,useState} from 'react';
import Markdown,{type Components} from 'react-markdown';
import remarkGfm from 'remark-gfm';

let activeImages=0;
const imageWaiters:Array<()=>void>=[];
async function loadImage(src:string,live:()=>boolean){
 if(activeImages>=3)await new Promise<void>(resolve=>imageWaiters.push(resolve));else activeImages++;
 try {if(!live())throw Error('cancelled');return await window.biodsh.cloudQa('image',{url:src});}
 finally {const next=imageWaiters.shift();if(next)next();else activeImages--;}
}
function imageError(value:unknown){
 const message=String(value);
 if(message.includes('UNAUTHORIZED'))return '登录已过期，请重新登录后查看图片。';
 if(message.includes('IMAGE_DENIED'))return '图片链接已失效或访问被拒绝，请重新获取回答中的图片。';
 if(message.includes('IMAGE_MISSING'))return '文献图片暂不可用，请稍后重新加载。';
 return '图片暂时无法加载，请稍后重试。';
}
function KnowledgeImage({src,alt}:{src?:string;alt?:string}){
 const [image,setImage]=useState(''),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 const request=useRef<{key:string;promise:Promise<unknown>}|null>(null);
 const current=useRef('');
 useEffect(()=>{let alive=true;setImage('');setError('');
  if(!src){setError('图片地址无效');return;}
  const key=src+':'+attempt;
  current.current=key;
  if(request.current?.key!==key)request.current={key,promise:loadImage(src,()=>current.current===key)};
  void request.current.promise.then(value=>{
   const data=(value as {src?:unknown})?.src;
   if(typeof data!=='string'||!/^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/.test(data))throw Error('图片加载失败');
   if(alive)setImage(data);
  }).catch(value=>{if(alive)setError(imageError(value));});
  return()=>{alive=false;current.current='';};
 },[src,attempt]);
 return <span className="block my-3">{image?<img src={image} alt={alt||'文献图片'} className="max-w-full h-auto rounded" onError={()=>{setImage('');setError('图片无法显示，请重试');}}/>:error?<span role="status">{error}<button type="button" className="btn ml-2" onClick={()=>setAttempt(v=>v+1)}>重新加载图片</button></span>:<span role="status">正在加载文献图片…</span>}</span>;
}
const components:Components={
  img:({src,alt})=><KnowledgeImage src={src} alt={alt}/>,
  a:({href,children})=>{let safe=false;try{const u=new URL(href||'');safe=['https:','http:'].includes(u.protocol)&&!u.username&&!u.password;}catch{}
   return safe?<a href={href} className="text-blue-600 underline" onClick={e=>{e.preventDefault();void window.biodsh.openExternal(href!).catch(()=>window.alert('链接无法打开，请稍后重试'));}}>{children}</a>:<span>{children}</span>;},
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
export default function KnowledgeMarkdown({text}:{text:string}){
 return <div className="mt-2 break-words leading-relaxed"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={components}>{text}</Markdown></div>;
}
