import {useT,translate,type Lang} from '../i18n';
import {forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import {BookOpen,MessageSquareText,Search,Send,Plus,RefreshCw,Trash2} from 'lucide-react';
import './CloudKnowledgeView.css';
import KnowledgeMarkdown from './KnowledgeMarkdown';
import {cloudRows,cloudSession,cloudMessages,cloudReply,cloudProgress,qaProgressLabel,type Source,type Message,type Session,type QaProcess} from './cloud-knowledge-data';
type StreamEvent={event:string;payload:unknown};
export type KnowledgeSourceState={sessions:Session[];sessionId:string|null;busy:boolean;unsaved:boolean;loading:boolean;error:string};
export type KnowledgeSourceHandle={selectSession:(id:string)=>Promise<void>;refreshSessions:()=>Promise<void>};
type Props={onContextChange?:(context:{sessionId:string|null;messages:Message[];busy:boolean})=>void;onSourceChange?:(state:KnowledgeSourceState)=>void};
const call=async <T,>(action:string,body:unknown={},onEvent?:(event:StreamEvent)=>void):Promise<T>=>await window.biodsh.cloudQa(action,body,onEvent) as T;
const processStatus={running:'正在检索文献',complete:'已完成',cancelled:'已中止',error:'处理失败'};
const persistedMessage=({role,text,blocks}:Message)=>({role,text,blocks});
export function sourceUrl(source:Source):string|null{
 const candidate=source.url||(source.doi?'https://doi.org/'+source.doi:source.pmid?'https://pubmed.ncbi.nlm.nih.gov/'+source.pmid+'/':'');
 try{const u=new URL(candidate);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null;}catch{return null;}
}
export function sourcesText(sources:Source[],lang:Lang='zh'):string{return sources.map((s,i)=>[`${i+1}. ${s.title||translate(lang,'文献')}`,s.snippet,s.doi&&`DOI: ${s.doi}`,s.pmid&&`PMID: ${s.pmid}`,sourceUrl(s)].filter(Boolean).join('\n')).join('\n\n');}
// Only app-authored search status is localized; source passages and model answers stay intact.
export function messageBody(message:Message,lang:Lang='zh'):string{
 const text=message.text||'';
 const searchStatus=message.role==='assistant'&&(message.blocks||[]).some(b=>b.type==='sources')&&(/^找到 \d+ 条文献来源。$/.test(text)||text==='暂未找到相关文献，可调整关键词后重试。');
 return searchStatus?translate(lang,text):text;
}
export function messageText(message:Message,lang:Lang='zh'):string{return [messageBody(message,lang),...(message.blocks||[]).map(b=>sourcesText(b.items||[],lang))].filter(Boolean).join('\n\n');}
export function processText(process:QaProcess,lang:Lang='zh'):string{return [translate(lang,'检索与生成过程')+' · '+translate(lang,processStatus[process.status]),...process.steps.map(step=>[translate(lang,qaProgressLabel(step)),...step.details.map(detail=>translate(lang,detail.label)+': '+detail.value)].join('\n'))].join('\n\n');}
function CopyButton({text,label}:{text:string;label:string}){
 const {t,lang}=useT();
 const [status,setStatus]=useState('');
 return <><button type="button" className="btn btn-ghost knowledge-copy" onClick={async()=>{try{await navigator.clipboard.writeText(text);setStatus('已复制');}catch{setStatus('复制失败，请选中文字复制');}}}>{label}</button>{status&&<span role="status" className="knowledge-copy-status">{t(status)}</span>}</>;
}
function Sources({sources}:{sources:Source[]}){
 const {t,lang}=useT();return <div className="flex flex-col gap-2 mt-3"><CopyButton text={sourcesText(sources,lang)} label={t("复制检索结果")}/>{sources.map((s,i)=><div className="p-3 rounded border" key={s.id||i}><strong>{s.title||s.doi||t('文献 {n}',{n:i+1})}</strong>{s.snippet&&<p className="text-sm whitespace-pre-wrap mt-1">{s.snippet}</p>}{(s.doi||s.pmid)&&<p className="text-xs mt-1">{s.doi?`DOI: ${s.doi}`:`PMID: ${s.pmid}`}</p>}{sourceUrl(s)&&<button className="btn btn-ghost mt-1" onClick={()=>void window.biodsh.openExternal(sourceUrl(s)!).catch(()=>window.alert(t('链接无法打开，请稍后重试')))}>{t("查看来源")}</button>}<CopyButton text={sourcesText([s],lang)} label={t("复制文献")}/></div>)}</div>;}
function Process({process,hasDraft}:{process:QaProcess;hasDraft:boolean}){
 const {t,lang}=useT(),[open,setOpen]=useState(process.status!=='complete');
 useEffect(()=>{if(process.status==='complete')setOpen(false);},[process.status]);
 const latest=process.steps.at(-1),active=process.status==='running';
 const status=active?(hasDraft?'正在生成回答':latest?qaProgressLabel(latest):process.mode==='search'?'正在检索文献':'等待检索服务响应'):processStatus[process.status];
 return <details className={'knowledge-process knowledge-process-'+process.status} open={open} onToggle={event=>setOpen(event.currentTarget.open)}>
  <summary><span className="knowledge-process-indicator" aria-hidden="true"/><strong>{t('检索与生成过程')}</strong><span className="knowledge-process-status" role={active?'status':undefined}>{t(status)}</span></summary>
  <div className="knowledge-process-body"><p className="knowledge-process-caption">{t('这里展示检索步骤和证据筛选进度。')}</p>
   {process.steps.length>0?<ol>{process.steps.map((step,index)=><li key={index}><div className="knowledge-process-step"><strong>{t(qaProgressLabel(step))}</strong>{step.at&&<time dateTime={step.at}>{new Date(step.at).toLocaleTimeString(lang==='en'?'en-US':'zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})}</time>}</div>{step.details.length>0&&<dl>{step.details.map((detail,j)=><div key={j}><dt>{t(detail.label)}</dt><dd>{detail.value}</dd></div>)}</dl>}</li>)}</ol>:<p>{t(active?status:processStatus[process.status])}</p>}
   <CopyButton text={processText(process,lang)} label={t('复制过程')}/>
  </div>
 </details>;
}
const CloudKnowledgeView=forwardRef<KnowledgeSourceHandle,Props>(function CloudKnowledgeView({onContextChange,onSourceChange},ref){
 const {t,lang}=useT();
 const [sessions,setSessions]=useState<Session[]>([]),[selected,setSelected]=useState<string|null>(null),[messages,setMessages]=useState<Message[]>([]);
 const [question,setQuestion]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[unsaved,setUnsaved]=useState<{id:string;answer:Message}|null>(null);
 const [loadingSessions,setLoadingSessions]=useState(false),[sourceError,setSourceError]=useState('');
 const [activeRequest,setActiveRequest]=useState<string|null>(null);const requestRef=useRef<string|null>(null);
 const alive=useRef(true),version=useRef(0),pending=useRef(false),listVersion=useRef(0);
 const streamRef=useRef<{requestId:string;message:Message;timer:ReturnType<typeof setTimeout>|null}|null>(null);
 const questionInput=useRef<HTMLTextAreaElement>(null),resultArea=useRef<HTMLDivElement>(null);
 const followResults=useRef(true);
 useImperativeHandle(ref,()=>({selectSession:select,refreshSessions:refresh}));
 useEffect(()=>{onContextChange?.({sessionId:selected,messages,busy});},[selected,messages,busy,onContextChange]);
 useEffect(()=>{onSourceChange?.({sessions,sessionId:selected,busy,unsaved:!!unsaved,loading:loadingSessions,error:sourceError});},[sessions,selected,busy,unsaved,loadingSessions,sourceError,onSourceChange]);
 useEffect(()=>{const area=resultArea.current;if(area&&followResults.current)area.scrollTop=area.scrollHeight;},[messages,selected]);
 useEffect(()=>{alive.current=true;void refresh();return()=>{alive.current=false;version.current++;if(streamRef.current?.timer)clearTimeout(streamRef.current.timer);streamRef.current=null;if(requestRef.current)void call('cancel',{requestId:requestRef.current}).catch(()=>{});};},[]);
 function finishStream(status:QaProcess['status'],publish=true){
  const stream=streamRef.current;if(!stream)return;
  if(stream.timer)clearTimeout(stream.timer);
  streamRef.current=null;
  const process=stream.message.process&&{...stream.message.process,status};
  if(publish&&alive.current)setMessages(previous=>previous.map(message=>message.id===stream.message.id?{...stream.message,process}:message));
  return {id:stream.message.id,process};
 }
 async function refresh(){const request=++listVersion.current;setLoadingSessions(true);setSourceError('');try{const v=await call<unknown>('list');if(alive.current&&request===listVersion.current)setSessions(cloudRows(v));}catch{if(alive.current&&request===listVersion.current){setError('会话列表暂时无法加载，请重试');setSourceError('会话列表暂时无法加载，请重试');}}finally{if(alive.current&&request===listVersion.current)setLoadingSessions(false);}}
 async function newSession(){
  if(pending.current||unsaved)return;
  pending.current=true;setBusy(true);setError('');setSourceError('');const revision=++version.current;
  try{const session=cloudSession(await call('create',{title:t('新问答 ')+new Date().toLocaleString(lang==='en'?'en-US':'zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})}));
   if(alive.current&&revision===version.current){followResults.current=true;setSelected(session.id);setMessages([]);setQuestion('');setSessions(prev=>[session,...prev.filter(s=>s.id!==session.id)]);void refresh();}
  }catch{if(alive.current)setError('新建问答失败，请重试；原会话已保留');}
  finally{pending.current=false;if(alive.current){setBusy(false);setTimeout(()=>questionInput.current?.focus(),0);}}
 }
 async function select(id:string){if(!id||id===selected||pending.current||unsaved)return;const revision=++version.current;setBusy(true);setError('');setSourceError('');pending.current=true;try{const d=cloudMessages(await call('detail',{id}));if(alive.current&&revision===version.current){followResults.current=true;setSelected(id);setMessages(d);}}catch{if(alive.current&&revision===version.current){setError('会话加载失败，请刷新后重试');setSourceError('会话加载失败，请刷新后重试');}}finally{if(alive.current&&revision===version.current){pending.current=false;setBusy(false);}}}
 function stop(){const requestId=requestRef.current;if(!requestId)return;const revision=++version.current;finishStream('cancelled');requestRef.current=null;setActiveRequest(null);pending.current=false;setBusy(false);setError('已取消本次查询');const unconfirmed=()=>{if(alive.current&&version.current===revision)setError('已停止接收回答，但云端取消未确认，查询可能仍在处理');};void call<{cloud_cancelled?:boolean}>('cancel',{requestId}).then(result=>{if(result.cloud_cancelled===false)unconfirmed();}).catch(unconfirmed);}
 async function ask(search:boolean){
  const q=question.trim();if(pending.current||unsaved)return;
  if(!q){questionInput.current?.focus();return;}
  pending.current=true;setBusy(true);setError('');const revision=++version.current;
  const valid=()=>alive.current&&version.current===revision;
  try{
   let id=selected;
   if(!id){const r=cloudSession(await call('create',{title:q.slice(0,60)}));id=r.id;if(!id)throw Error('会话创建失败');if(valid())setSelected(id);}
   await call('message',{id,role:'user',text:q});if(!valid())return;
   followResults.current=true;setMessages(prev=>[...prev,{role:'user',text:q}]);setQuestion('');
   const requestId=crypto.randomUUID();
   const draft:Message={id:'qa-'+requestId,role:'assistant',text:'',transient:true,process:{status:'running',mode:search?'search':'chat',steps:[]}};
   streamRef.current={requestId,message:draft,timer:null};setMessages(prev=>[...prev,draft]);
   await call('begin',{requestId});
   if(!valid()){void call('cancel',{requestId}).catch(()=>{});return;}
   requestRef.current=requestId;setActiveRequest(requestId);
   const onEvent=(event:StreamEvent)=>{
    const stream=streamRef.current;if(!valid()||!stream||stream.requestId!==requestId||requestRef.current!==requestId)return;
    if(event.event==='progress'){
     const step=cloudProgress(event.payload);if(!step)return;
     const process=stream.message.process!,previous=process.steps.at(-1);
     const duplicate=previous?.stage===step.stage&&previous.phase===step.phase&&JSON.stringify(previous.details)===JSON.stringify(step.details);
     if(duplicate)return;
     stream.message={...stream.message,process:{...process,steps:[...process.steps,step]}};
    }else if(event.event==='answer_delta'){
     const data=event.payload;if(!data||typeof data!=='object'||Array.isArray(data)||typeof (data as Record<string,unknown>).content!=='string')return;
     stream.message={...stream.message,text:(stream.message.text||'')+(data as {content:string}).content};
    }else return;
    if(!stream.timer)stream.timer=setTimeout(()=>{
     stream.timer=null;if(valid()&&streamRef.current===stream)setMessages(previous=>previous.map(message=>message.id===stream.message.id?stream.message:message));
    },50);
   };
   const result=cloudReply(await call(search?'search':'chat',{question:q,requestId},search?undefined:onEvent));if(!valid())return;
   requestRef.current=null;setActiveRequest(null);
   const sources=Array.isArray(result.sources)?result.sources:[];
   const text=search?(sources.length?`找到 ${sources.length} 条文献来源。`:'暂未找到相关文献，可调整关键词后重试。'):result.answer;
   if(!text)throw Error('知识库尚未返回答案，请稍后重试');
   const finished=finishStream('complete',false);
   const answer:Message={id:finished?.id,role:'assistant',text,blocks:[{type:'sources',items:sources}],...(!search&&finished?.process?{process:finished.process}:{})};setMessages(prev=>prev.map(message=>message.id===answer.id?answer:message));
   try{await call('message',{id,...persistedMessage(answer)});}catch{if(valid())setUnsaved({id,answer});}
   if(valid())void refresh();
  }catch(e){if(valid()){const message=String(e).replace(/^Error:\s*/,''),cancelled=message.toUpperCase()==='QUERY_CANCELLED';finishStream(cancelled?'cancelled':'error');if(!cancelled)setQuestion(q);setError(cancelled?'已取消本次查询':message.replace(/^(UNAUTHORIZED|FORBIDDEN|QA_[A-Z_]+):\s*/,'').slice(0,180));}}
  finally{if(valid()){requestRef.current=null;setActiveRequest(null);pending.current=false;setBusy(false);setTimeout(()=>questionInput.current?.focus(),0);}}
 }
 async function retrySave(){
  if(!unsaved||pending.current)return;pending.current=true;setBusy(true);setError('');
  try{
   const history=cloudMessages(await call('detail',{id:unsaved.id}));
   const last=history.at(-1),answer=cloudMessages({messages:[unsaved.answer]})[0];
   // A lost response can follow a successful write; reconcile before sending again.
   if(!last||last.role!=='assistant'||last.text!==answer.text||JSON.stringify(last.blocks)!==JSON.stringify(answer.blocks))await call('message',{id:unsaved.id,...persistedMessage(unsaved.answer)});
   if(alive.current){setUnsaved(null);void refresh();}
  }
  catch{if(alive.current)setError('保存仍未完成，请稍后重试或复制回答。');}
  finally{pending.current=false;if(alive.current)setBusy(false);}
 }
 async function remove(){if(!selected||pending.current)return;pending.current=true;setBusy(true);setError('');try{await call('delete',{id:selected});if(alive.current){setSelected(null);setMessages([]);setUnsaved(null);void refresh();}}catch(e){if(alive.current)setError(String(e).slice(0,160));}finally{pending.current=false;if(alive.current)setBusy(false);}}
 return <section className="knowledge-page" aria-label={t("知识库问答")}>
 <header className="knowledge-header">
  <div className="knowledge-brand"><span className="knowledge-brand-icon"><BookOpen size={23}/></span><div><h1>{t("文献知识库问答")}</h1><p>{t("查找研究依据，让每个回答都有文献可循。")}</p></div></div>
  <div className="knowledge-session-bar"><label className="knowledge-session-picker"><span>{t("历史问答")}</span><select className="field" aria-label={t("知识库会话")} value={selected||''} disabled={busy||!!unsaved} onChange={e=>e.target.value&&void select(e.target.value)}><option value="">{t("新会话")}</option>{sessions.map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</select></label><button className="btn btn-tint" disabled={busy||!!unsaved} onClick={()=>void newSession()}><Plus size={16}/>{t("新建问答")}</button><button className="btn btn-ghost knowledge-icon-button" aria-label={t("刷新会话")} title={t("刷新会话")} disabled={busy} onClick={()=>void refresh()}><RefreshCw size={16}/></button><button className="btn btn-ghost knowledge-icon-button" aria-label={t("删除会话")} title={t("删除会话")} disabled={busy||!selected} onClick={()=>void remove()}><Trash2 size={16}/></button></div>
 </header>
 <div className="knowledge-results" ref={resultArea} role="region" aria-label={t("问答结果")} tabIndex={0} onScroll={event=>{const area=event.currentTarget;followResults.current=area.scrollHeight-area.scrollTop-area.clientHeight<80;}}>
  <div className="knowledge-thread">
   {!messages.length&&<div className="knowledge-empty"><span className="knowledge-empty-icon"><MessageSquareText size={32}/></span><h2>{t("从一个科研问题开始")}</h2><p>{t("输入研究问题获取文献支持的回答，或只检索相关文献。")}</p><div className="knowledge-suggestions">{[t('水稻 SPL 基因家族有哪些主要功能？'),t('单细胞 RNA-seq 分析常用哪些质控方法？'),t('如何查找植物抗逆相关研究？')].map(example=><button type="button" key={example} disabled={busy||!!unsaved} onClick={()=>{setQuestion(example);setError('');questionInput.current?.focus();}}><Search size={14}/><span>{example}</span></button>)}</div></div>}
   {messages.map((m,i)=><article className={'knowledge-message '+(m.role==='user'?'knowledge-message-user':'knowledge-message-answer')} key={m.id||i}>
    <div className="knowledge-message-heading">{m.role==='user'?<MessageSquareText size={16}/>:<BookOpen size={16}/>}<strong>{m.role==='user'?t('我的问题'):m.transient?t('已接收的部分回答'):t('知识库回答')}</strong>{messageText(m,lang)&&<CopyButton text={messageText(m,lang)} label={m.role==='user'?t('复制问题'):m.transient?t('复制部分回答'):t('复制回答和来源')}/>}</div>
    {m.process&&<Process process={m.process} hasDraft={!!m.text}/>}
    {m.transient?<>{m.text&&<p className="knowledge-draft">{m.text}</p>}{m.process?.status!=='running'&&<p className="knowledge-partial-notice">{t('部分回答尚未完成，仅保留在当前页面，可复制保存。')}</p>}</>:<KnowledgeMarkdown text={messageBody(m,lang)} sessionId={selected}/>}
    {m.blocks?.map((b,j)=>{const sources=b.items||[];return sources.length?<details className="knowledge-sources" key={j}><summary>{t('参考文献（{n}）',{n:sources.length})}</summary><Sources sources={sources}/></details>:null;})}
   </article>)}
  </div>
 </div>
 <footer className="knowledge-composer-area">
  <div className="knowledge-composer-inner">
   {unsaved&&<div className="knowledge-notice"><p role="status">{t("回答已显示，但保存失败。请重试保存，或复制需要的内容后再放弃保存。退出账号或关闭软件会丢失未保存回答。")}</p><button className="btn" disabled={busy} onClick={()=>void retrySave()}>{t("重试保存")}</button><button className="btn" disabled={busy} onClick={()=>{if(window.confirm(t('确认放弃保存这条回答？请先复制需要的内容。')))setUnsaved(null);}}>{t("放弃保存")}</button></div>}
   {error&&<p role={error==='已取消本次查询'?'status':'alert'} className={error==='已取消本次查询'?'knowledge-footnote':'knowledge-error'}>{t(error.replace(/^Error:\s*/,''))}</p>}
   <form className="knowledge-composer" onSubmit={e=>{e.preventDefault();void ask(false);}}>
    <label htmlFor="knowledge-question">{t("科研问题")}</label>
    <textarea ref={questionInput} id="knowledge-question" rows={2} maxLength={8000} value={question} disabled={busy||!!unsaved} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing&&e.nativeEvent.keyCode!==229){e.preventDefault();void ask(false);}}} placeholder={t("输入科研问题或文献关键词…")}/>
    <div className="knowledge-composer-actions"><div className="knowledge-submit-buttons"><button type="button" className="btn btn-fill" title={t("只查找文献来源，不生成问答内容")} disabled={busy||!!unsaved||!question.trim()} onClick={()=>void ask(true)}><Search size={15}/>{t("仅检索文献")}</button>{activeRequest?<button type="button" className="btn btn-primary" onClick={stop}>{t("中止")}</button>:<button type="submit" className="btn btn-primary" title={t("检索文献并生成回答")} disabled={busy||!!unsaved||!question.trim()}><Send size={15}/>{busy?t('正在处理，请稍候…'):t('提问')}</button>}</div></div>
   </form>
   <p className="knowledge-footnote">{t("每个问题独立检索，会话保存问答历史。引用前请展开参考文献核对原文。")}</p>
  </div>
 </footer>
 </section>;
});
export default CloudKnowledgeView;
