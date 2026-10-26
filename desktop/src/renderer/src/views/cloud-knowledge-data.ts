import TurndownService from 'turndown';
export type Source={id?:string;title?:string;doi?:string;pmid?:string;url?:string;snippet?:string};
export type QaProgress={stage:string;phase:'planning'|'retrieval'|'evidence'|'generation'|'done';details:{label:string;value:string}[];at?:string};
export type QaProcess={status:'running'|'complete'|'cancelled'|'error';mode:'chat'|'search';steps:QaProgress[]};
// Process and transient are view-only metadata; never send them to cloud history.
export type Message={id?:string;role:string;text?:string;blocks?:{type:string;items:Source[]}[];process?:QaProcess;transient?:boolean};
export type Session={id:string;title:string};
export const qaStageLabels:Record<string,string>={
 understanding_started:'开始理解问题',entities_identified:'已识别实体',related_search_started:'开始关联检索',
 concepts_found:'发现相关概念',subquestion_retrieved:'子问题召回完成',agent_review_started:'开始智能体审核',
 subquestion_retry_started:'子问题重试中',evidence_accepted:'证据已采纳',evidence_review_completed:'证据审核完成',
 web_search_started:'开始联网检索',web_search_completed:'联网检索完成',answer_generation_started:'开始生成答案',completed:'检索完成',
};
const phaseLabels={planning:'正在理解问题',retrieval:'正在检索文献',evidence:'正在筛选证据',generation:'正在生成回答',done:'检索完成'};
export function qaProgressLabel(step:QaProgress):string{return qaStageLabels[step.stage]||phaseLabels[step.phase];}
export function cloudProgress(value:unknown):QaProgress|null{
 if(!value||typeof value!=='object'||Array.isArray(value))return null;
 const raw=value as Record<string,unknown>;
 if(typeof raw.stage!=='string'&&typeof raw.phase!=='string')return null;
 const phase=typeof raw.phase==='string'&&Object.hasOwn(phaseLabels,raw.phase)?raw.phase as QaProgress['phase']:'retrieval';
 const details:QaProgress['details']=[],source=raw.details&&typeof raw.details==='object'&&!Array.isArray(raw.details)?raw.details as Record<string,unknown>:{};
 const publicName=(item:unknown):string=>{
  if(typeof item==='string')return item.slice(0,500);
  if(item&&typeof item==='object'&&!Array.isArray(item)){const entry=item as Record<string,unknown>;return typeof entry.name==='string'?entry.name.slice(0,500):typeof entry.label==='string'?entry.label.slice(0,500):'';}
  return '';
 };
 for(const [key,label] of [['entities','识别实体'],['concepts','相关概念'],['subquestion','子问题']] as const){
  const item=source[key],value=Array.isArray(item)?item.slice(0,20).map(publicName).filter(Boolean).join(' · '):publicName(item);
  if(value)details.push({label,value});
 }
 for(const [key,label] of [['accepted_count','采纳证据'],['retrieved_count','检索结果'],['source_count','文献来源'],['count','数量']] as const){
  const count=source[key];if(typeof count==='number'&&Number.isFinite(count)&&count>=0)details.push({label,value:String(count)});
 }
 // The public stage codes and allowlisted fields are sufficient; do not render raw reasoning or arbitrary labels.
 return {stage:typeof raw.stage==='string'&&Object.hasOwn(qaStageLabels,raw.stage)?raw.stage:'unknown',phase,details,
  ...(typeof raw.at==='string'&&/^\d{4}-\d\d-\d\dT/.test(raw.at)&&Number.isFinite(Date.parse(raw.at))?{at:raw.at}:{})};
}
const invalid=()=>new Error('知识库返回的数据不完整，请稍后重试');
function object(value:unknown):Record<string,unknown>{
 if(!value||typeof value!=='object'||Array.isArray(value))throw invalid();
 return value as Record<string,unknown>;
}
function array(value:unknown):unknown[]{if(!Array.isArray(value))throw invalid();return value;}
function string(value:unknown,fallback=''):string{if(value==null)return fallback;if(typeof value!=='string')throw invalid();return value;}
export function cloudSession(value:unknown):Session{
 const raw=object(value),s=object(raw.session??raw);const id=string(s.id);if(!id)throw invalid();
 return {id,title:string(s.title,'知识库问答')};
}
export function cloudRows(value:unknown):Session[]{
 const rows=Array.isArray(value)?value:object(value).sessions??object(value).items;
 return array(rows).map(cloudSession);
}
export function cloudSources(value:unknown):Source[]{
 return array(value??[]).map(entry=>{const s=object(entry);return {
  id:typeof s.id==='number'?String(s.id):string(s.id),title:string(s.title),doi:string(s.doi),
  pmid:typeof s.pmid==='number'?String(s.pmid):string(s.pmid),url:string(s.url),snippet:string(s.snippet),
 };});
}
export function cloudMessages(value:unknown):Message[]{
 const raw=object(value),detail=object(raw.session??raw);
 return array(detail.messages).map(entry=>{const m=object(entry);return {
  id:string(m.id),role:string(m.role),text:messageText(m),
  blocks:array(m.blocks??[]).flatMap(entry=>{const b=object(entry);return b.type==='sources'?[{type:'sources',items:cloudSources(b.items??b.sources)}]:[];}),
 };});
}
function messageText(message:Record<string,unknown>):string{
 const text=string(message.text);if(text)return text;
 return array(message.blocks??[]).flatMap(entry=>{
  const block=object(entry);if(block.type!=='text')return [];
  const html=string(block.html);const doc=new DOMParser().parseFromString(html,'text/html');
  const markdown=doc.querySelector('[data-omaax-markdown="1"]');
  if(markdown)return [markdown.textContent||''];
  doc.querySelectorAll('script,style,iframe,object,embed').forEach(node=>node.remove());
  return [new TurndownService().turndown(doc.body.innerHTML)];
 }).join('\n\n');
}
export function cloudReply(value:unknown){const r=object(value);if(r.success===false||r.ok===false)throw invalid();return {answer:string(r.answer),sources:cloudSources(r.sources)};}
