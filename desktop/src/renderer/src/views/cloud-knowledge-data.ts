import TurndownService from 'turndown';
export type Source={id?:string;title?:string;doi?:string;pmid?:string;url?:string;snippet?:string};
export type Message={id?:string;role:string;text?:string;blocks?:{type:string;items:Source[]}[]};
export type Session={id:string;title:string};
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
