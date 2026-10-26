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
 const modules=new Map(),events=new EventTarget(),data=new Map(),timers=[],timeouts=new Map();let now=0,nextTimer=0;
 const schedule=(fn,delay=0,...args)=>{const id=++nextTimer;timeouts.set(id,{fn:()=>fn(...args),at:now+delay});return id;};
 const clear=id=>timeouts.delete(id);
 globalThis.sessionStorage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};
 globalThis.localStorage=globalThis.sessionStorage;
 globalThis.window={biodsh:api,addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events),dispatchEvent:events.dispatchEvent.bind(events),setInterval:fn=>{timers.push(fn);return timers.length;},clearInterval:()=>{},setTimeout:schedule,clearTimeout:clear,confirm:()=>true};
 const load=file=>{
  if(modules.has(file))return modules.get(file).exports;const module={exports:{}};modules.set(file,module);
  if(file.endsWith('.json')){module.exports=JSON.parse(fs.readFileSync(file,'utf8'));return module.exports;}
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
  const require=name=>{
   if(name.endsWith('.css'))return {};if(name==='./KnowledgeMarkdown')return {__esModule:true,default:({text})=>React.createElement('p',null,text)};if(name==='turndown')return realRequire(name);if(name==='react')return React;if(name==='react/jsx-runtime')return realRequire(name);
   if(name==='lucide-react')return new Proxy({},{get:(_,key)=>key==='__esModule'?false:()=>null});
   if(name.endsWith('/store'))return {useApp:()=>({currentSession:'test-dialogue',setTab:()=>{}})};
   if(name.endsWith('/i18n'))return {useT:()=>({lang:'zh',t:value=>value}),translate:(_lang,value)=>value};
   if(name.startsWith('.')){const p=path.resolve(path.dirname(file),name);return load(['','.ts','.tsx'].map(e=>p+e).find(p=>fs.existsSync(p)));}
   throw Error('Unexpected dependency '+name);
  };
  new Function('require','module','exports','setInterval','clearInterval','setTimeout','clearTimeout',code)(require,module,module.exports,fn=>{timers.push(fn);return timers.length;},()=>{},schedule,clear);return module.exports;
 };
 return {load:relative=>load(path.join(root,'src/renderer/src',relative)),events,timers,tick(ms){now+=ms;for(const [id,timer] of timeouts){if(timer.at<=now){timeouts.delete(id);timer.fn();}}},pendingTimers:()=>timeouts.size};
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

test('cloud cancellation is a normal status and permits a new query',async()=>{
 const h=harness({cloudQa:async(action)=>{if(action==='list')return [];if(action==='create')return {id:'s1'};if(action==='chat')throw Error('QUERY_CANCELLED');return {ok:true};}});
 const View=h.load('views/CloudKnowledgeView.tsx').default;let view;
 await act(async()=>{view=create(React.createElement(View));await settle();});
 await act(async()=>view.root.findByType('textarea').props.onChange({target:{value:'test'}}));
 await act(async()=>{view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});
 assert.ok(view.root.findAll(n=>n.props.role==='status').some(n=>text(n)==='已取消本次查询'));
 assert.equal(view.root.findAll(n=>n.props.role==='alert').length,0);
 assert.equal(view.root.findByType('textarea').props.disabled,false);
 assert.doesNotMatch(text(view.root),/QUERY_CANCELLED/);
 await act(async()=>view.unmount());
});

test('failed remote cancellation warns without accepting a late answer',async()=>{
 let reply;
 const h=harness({cloudQa:async(action)=>{if(action==='list')return [];if(action==='create')return {id:'s1'};if(action==='chat')return new Promise(resolve=>reply=resolve);if(action==='cancel')throw Error('offline');return {ok:true};}});
 const View=h.load('views/CloudKnowledgeView.tsx').default;let view;
 await act(async()=>{view=create(React.createElement(View));await settle();});
 await act(async()=>view.root.findByType('textarea').props.onChange({target:{value:'test'}}));
 await act(async()=>{view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});
 await act(async()=>{button(view,'中止').props.onClick();await settle();});
 assert.match(text(view.root),/云端取消未确认/);
 await act(async()=>{reply({answer:'STALE',sources:[]});await settle();});
 assert.doesNotMatch(text(view.root),/STALE/);
 assert.equal(view.root.findByType('textarea').props.disabled,false);
 await act(async()=>view.unmount());
});

test('cloud without a tracked request never reports confirmed cancellation',async()=>{
 const h=harness({cloudQa:async(action)=>{if(action==='list')return [];if(action==='create')return {id:'s1'};if(action==='search')return new Promise(()=>{});if(action==='cancel')return {ok:true,cloud_cancelled:false};return {ok:true};}});
 const View=h.load('views/CloudKnowledgeView.tsx').default;let view;
 await act(async()=>{view=create(React.createElement(View));await settle();});
 await act(async()=>view.root.findByType('textarea').props.onChange({target:{value:'test'}}));
 await act(async()=>{button(view,'仅检索文献').props.onClick();await settle();});
 await act(async()=>{button(view,'中止').props.onClick();await settle();});
 assert.match(text(view.root),/云端取消未确认/);
 assert.equal(view.root.findByType('textarea').props.disabled,false);
 await act(async()=>view.unmount());
});

async function streamHarness(){
 const calls=[],requests=[];let copied='',view;
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async value=>{copied=value;}}}});
 const h=harness({cloudQa:async(action,body,onEvent)=>{
  calls.push([action,body]);
  if(action==='list')return [];
  if(action==='create')return {id:'stream-session',title:'Streaming test'};
  if(action==='chat')return new Promise((resolve,reject)=>requests.push({onEvent,resolve,reject}));
  return {ok:true};
 }});
 const View=h.load('views/CloudKnowledgeView.tsx').default;
 await act(async()=>{view=create(React.createElement(View));await settle();});
 return {...h,view,calls,requests,copied:()=>copied,async ask(question='question'){
  await act(async()=>view.root.findByType('textarea').props.onChange({target:{value:question}}));
  await act(async()=>{view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});
 }};
}

test('retrieval progress precedes the answer; batched deltas are replaced by the authoritative completion',async()=>{
 const h=await streamHarness(),{view}=h;
 try{
  await h.ask();const request=h.requests[0];
  assert.equal(typeof request.onEvent,'function');
  assert.match(text(view.root),/检索与生成过程/);
  await act(async()=>{request.onEvent({event:'progress',payload:{stage:'understanding_started',phase:'planning',label:'开始理解问题',details:{entities:['水稻']}}});h.tick(50);});
  assert.match(text(view.root),/开始理解问题/);
  assert.equal(h.calls.filter(([action,body])=>action==='message'&&body.role==='assistant').length,0);

  await act(async()=>{for(const content of ['DRAFT-',...Array(1000).fill('片段')])request.onEvent({event:'answer_delta',payload:{content}});});
  assert.doesNotMatch(text(view.root),/DRAFT-/,'delta bursts must not repaint synchronously');
  await act(async()=>h.tick(49));assert.doesNotMatch(text(view.root),/DRAFT-/);
  await act(async()=>h.tick(1));assert.ok(text(view.root).includes('DRAFT-'+'片段'.repeat(1000)));
  await act(async()=>{request.onEvent({event:'evidence',payload:{sources:[{title:'Preliminary source',snippet:'Early evidence'}]}});h.tick(50);});
  await act(async()=>{request.resolve({answer:'FINAL authoritative answer',sources:[{title:'Final paper',doi:'10.123/final',snippet:'Verified evidence'}]});await settle();});
  await act(async()=>h.tick(100));
  assert.match(text(view.root),/FINAL authoritative answer/);
  assert.doesNotMatch(text(view.root),/DRAFT-|Preliminary source/);
  assert.equal(button(view,'中止'),undefined);
  const saved=h.calls.filter(([action,body])=>action==='message'&&body.role==='assistant');
  assert.equal(saved.length,1);assert.equal(saved[0][1].text,'FINAL authoritative answer');
  assert.equal(saved[0][1].process,undefined,'local progress is not a cloud history field');
  assert.equal(saved[0][1].transient,undefined);
  assert.deepEqual(saved[0][1].blocks.map(block=>block.type),['sources']);
  await act(async()=>button(view,'复制回答和来源').props.onClick());
  assert.match(h.copied(),/FINAL authoritative answer/);assert.match(h.copied(),/Final paper/);assert.match(h.copied(),/https:\/\/doi.org\/10.123\/final/);
  await act(async()=>button(view,'复制过程').props.onClick());assert.match(h.copied(),/开始理解问题/);
 }finally{await act(async()=>view.unmount());}
});

test('cancelling retains the pending partial text while late stream events cannot contaminate the next query',async()=>{
 const h=await streamHarness(),{view}=h;
 try{
  await h.ask('first');const old=h.requests[0];
  await act(async()=>old.onEvent({event:'answer_delta',payload:{content:'Kept partial answer'}}));
  await act(async()=>{button(view,'中止').props.onClick();await settle();});
  assert.match(text(view.root),/Kept partial answer/);
  assert.equal(h.calls.filter(([action])=>action==='cancel').length,1);
  assert.equal(h.calls.filter(([action,body])=>action==='message'&&body.role==='assistant').length,0);
  await h.ask('second');const current=h.requests[1];
  await act(async()=>{
   old.onEvent({event:'answer_delta',payload:{content:'STALE delta'}});
   old.onEvent({event:'progress',payload:{stage:'stale',phase:'retrieval',label:'STALE progress'}});
   old.resolve({answer:'STALE complete',sources:[]});
   current.onEvent({event:'answer_delta',payload:{content:'Current partial'}});
   await settle();h.tick(50);
  });
  assert.doesNotMatch(text(view.root),/STALE/);assert.match(text(view.root),/Current partial/);assert.ok(button(view,'中止'));
  await act(async()=>{current.resolve({answer:'Current completed',sources:[]});await settle();});
  assert.match(text(view.root),/Current completed/);assert.doesNotMatch(text(view.root),/Current partial/);
  const saved=h.calls.filter(([action,body])=>action==='message'&&body.role==='assistant');
  assert.equal(saved.length,1);assert.equal(saved[0][1].text,'Current completed');
 }finally{await act(async()=>view.unmount());}
});

test('a failed stream keeps copyable partial output and progress without saving it as a complete answer',async()=>{
 const h=await streamHarness(),{view}=h;
 try{
  await h.ask('recoverable question');const request=h.requests[0];
  await act(async()=>{
   request.onEvent({event:'progress',payload:{stage:'evidence_accepted',phase:'evidence',label:'证据已采纳',details:{accepted_count:3}}});
   request.onEvent({event:'answer_delta',payload:{content:'Incomplete answer preserved'}});
   request.reject(Error('检索连接意外关闭，请重试'));
   await settle();h.tick(50);
  });
  assert.match(text(view.root),/Incomplete answer preserved/);assert.match(text(view.root),/证据已采纳/);assert.match(text(view.root),/检索连接意外关闭/);
  assert.equal(button(view,'中止'),undefined);assert.equal(view.root.findByType('textarea').props.disabled,false);
  assert.equal(view.root.findByType('textarea').props.value,'recoverable question');
  assert.equal(h.calls.filter(([action,body])=>action==='message'&&body.role==='assistant').length,0);
  const copyAnswer=view.root.findAllByType('button').find(node=>/复制/.test(text(node))&&/回答/.test(text(node)));
  assert.ok(copyAnswer);await act(async()=>copyAnswer.props.onClick());assert.match(h.copied(),/Incomplete answer preserved/);
  await act(async()=>button(view,'复制过程').props.onClick());assert.match(h.copied(),/证据已采纳/);
 }finally{await act(async()=>view.unmount());}
});

test('legacy saved answers and sources still load without a fabricated retrieval process',async()=>{
 let copied='';
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async value=>{copied=value;}}}});
 const h=harness({cloudQa:async action=>{
  if(action==='list')return [{id:'legacy',title:'Existing history'}];
  if(action==='detail')return {messages:[{role:'user',text:'Original question'},{role:'assistant',text:'Existing saved answer',blocks:[{type:'sources',items:[{title:'Existing source',doi:'10.123/legacy',snippet:'Original evidence'}]}]}]};
  return {ok:true};
 }});
 const View=h.load('views/CloudKnowledgeView.tsx').default;let view;
 await act(async()=>{view=create(React.createElement(View));await settle();});
 try{
  await act(async()=>{view.root.findByType('select').props.onChange({target:{value:'legacy'}});await settle();});
  assert.match(text(view.root),/Original question/);assert.match(text(view.root),/Existing saved answer/);assert.match(text(view.root),/Existing source/);
  assert.equal(button(view,'复制过程'),undefined);
  await act(async()=>button(view,'复制回答和来源').props.onClick());assert.match(copied,/Original evidence/);assert.match(copied,/10.123\/legacy/);
 }finally{await act(async()=>view.unmount());}
});

test('progress shows public stages, entities and evidence counts without leaking arbitrary raw reasoning fields',()=>{
 const h=harness({}),{cloudProgress,qaProgressLabel}=h.load('views/cloud-knowledge-data.ts');
 assert.equal(cloudProgress(null),null);assert.equal(cloudProgress([]),null);assert.equal(cloudProgress({}),null);
 const step=cloudProgress({stage:'evidence_accepted',phase:'evidence',label:'RAW INTERNAL LABEL',at:'invalid-time',details:{entities:['Rice',{name:'SPL'},{label:'Drought'}],accepted_count:3,source_count:-1,retrieved_count:Infinity,reasoning:'RAW INTERNAL REASONING',prompt:'RAW INTERNAL PROMPT'}});
 assert.equal(qaProgressLabel(step),'证据已采纳');
 assert.deepEqual(step.details,[{label:'识别实体',value:'Rice · SPL · Drought'},{label:'采纳证据',value:'3'}]);
 assert.equal(step.at,undefined);assert.doesNotMatch(JSON.stringify(step),/RAW INTERNAL/);
 const unknown=cloudProgress({stage:'new_server_stage',phase:'new_server_phase',label:'RAW UNKNOWN LABEL'});
 assert.equal(qaProgressLabel(unknown),'正在检索文献');assert.doesNotMatch(JSON.stringify(unknown),/RAW UNKNOWN/);
});
