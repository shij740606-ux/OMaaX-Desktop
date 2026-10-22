import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
const root=fileURLToPath(new URL('../',import.meta.url)),realRequire=createRequire(path.join(root,'package.json'));
const React=realRequire('react'),{act,create}=realRequire('react-test-renderer');
const text=node=>typeof node==='string'?node:(node.children||[]).map(text).join('');
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
function harness(api){
 const modules=new Map(),events=new EventTarget(),data=new Map(),timers=[];
 globalThis.sessionStorage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};
 globalThis.localStorage=globalThis.sessionStorage;
 globalThis.window={biodsh:api,addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events),dispatchEvent:events.dispatchEvent.bind(events),setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval:()=>{},confirm:()=>true};
 const load=file=>{
  if(modules.has(file))return modules.get(file).exports;const module={exports:{}};modules.set(file,module);
  if(file.endsWith('.json')){module.exports=JSON.parse(fs.readFileSync(file,'utf8'));return module.exports;}
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
  const require=name=>{
   if(name.endsWith('.css'))return {};if(name==='./KnowledgeMarkdown')return {__esModule:true,default:({text})=>React.createElement('p',null,text)};if(name==='turndown')return realRequire(name);if(name==='react')return React;if(name==='react/jsx-runtime')return realRequire(name);
   if(name==='lucide-react')return new Proxy({},{get:(_,key)=>key==='__esModule'?false:()=>null});
   if(name.endsWith('/store'))return {useApp:()=>({currentSession:'test-dialogue',setTab:()=>{}})};
   if(name.endsWith('/i18n'))return {useT:()=>({t:value=>value})};
   if(name.startsWith('.')){const p=path.resolve(path.dirname(file),name);return load(['','.ts','.tsx'].map(e=>p+e).find(p=>fs.existsSync(p)));}
   throw Error('Unexpected dependency '+name);
  };
  new Function('require','module','exports','setInterval','clearInterval',code)(require,module,module.exports,fn=>{timers.push(fn);return timers.length;},()=>{});return module.exports;
 };
 return {load:relative=>load(path.join(root,'src/renderer/src',relative)),events,timers};
}

const button=(view,label)=>view.root.findAllByType('button').find(b=>text(b)===label);

test('stop discards late answer without unlocking the next request; copy includes sources',async()=>{
 const calls=[],replies=[];let copied='';
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async t=>{copied=t;}}}});
 const h=harness({cloudQa:async(action,body)=>{calls.push([action,body]);if(action==='list')return [];if(action==='create')return {id:'s1',title:'test'};if(['chat','search'].includes(action))return await new Promise(resolve=>replies.push(resolve));return {ok:true};}});
 const View=h.load('views/CloudKnowledgeView.tsx').default;let view;
 await act(async()=>{view=create(React.createElement(View));await settle();});
 const ask=async(search=false)=>{await act(async()=>view.root.findByType('textarea').props.onChange({target:{value:'test question'}}));await act(async()=>{if(search)button(view,'仅检索文献').props.onClick();else view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});};
 await ask();assert.ok(button(view,'中止'));await act(async()=>button(view,'中止').props.onClick());assert.equal(calls.filter(c=>c[0]==='cancel').length,1);
 await ask(true);await act(async()=>{replies[0]({answer:'STALE',sources:[]});await settle();});assert.ok(button(view,'中止'));assert.doesNotMatch(text(view.root),/STALE/);
 await act(async()=>{replies[1]({sources:[{title:'Paper',doi:'10.123/test',snippet:'Evidence'}]});await settle();});assert.equal(button(view,'中止'),undefined);
 await act(async()=>button(view,'复制回答和来源').props.onClick());assert.match(copied,/Paper/);assert.match(copied,/Evidence/);assert.match(copied,/https:\/\/doi.org/);
 await act(async()=>button(view,'复制检索结果').props.onClick());assert.match(copied,/10.123/);
 await ask(true);await act(async()=>button(view,'中止').props.onClick());assert.equal(calls.filter(c=>c[0]==='cancel').length,2);
 await ask();await act(async()=>view.unmount());assert.equal(calls.filter(c=>c[0]==='cancel').length,3);
});
