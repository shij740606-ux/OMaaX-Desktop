import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import React from 'react';
import {act,create} from 'react-test-renderer';

const require=createRequire(import.meta.url),root=fileURLToPath(new URL('../src/renderer/src/views/',import.meta.url));
const settle=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
const text=node=>typeof node==='string'?node:(Array.isArray(node)?node:node?.children||[]).map(text).join(' ');
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const project=(name='A')=>({workspaceId:name,path:'C:/Project'+name,title:name,sessionIds:[]});
const evidence=(id='e1',extra={})=>({id,kind:'paper',title:'Paper '+id,detail:'An observed result',doi:'10.1000/'+id,...extra});
const analysis={title:'Research plan',summary:'Source-backed relationships and hypotheses',nodes:[{id:'n1',kind:'finding',label:'Observed relationship',summary:'Inspect the actual source',basis:'source',evidenceIds:['e1']}],edges:[],suggestions:[{id:'s1',title:'Validate the hypothesis',reason:'Current evidence is incomplete',requirements:['Independent samples'],steps:['Follow the registered analysis workflow'],evidenceIds:['e1'],limitations:'This is a hypothesis, not a confirmed result.'}]};
const saved=(extra={})=>({version:1,revision:4,topic:'Research question',evidence:[evidence()],analysis:null,analysisStale:false,updatedAt:'2026-10-26T00:00:00Z',...extra});
const simpleLayout=analysis=>({positions:Object.fromEntries(analysis.nodes.map((node,i)=>[node.id,{x:i*300,y:0}])),paths:Object.fromEntries(analysis.edges.map(edge=>[edge.id,'M 0 0 L 300 0']))});
function fixture(research,{handoff=async()=>{},incoming=[],initial=saved(),layout=async analysis=>simpleLayout(analysis),graphThrows=()=>false}={}){
 const calls=[],dirty=[],layouts=[],listeners=new Set(),modules=new Map();
 globalThis.window={confirm:()=>true,biodsh:{research:async(action,body)=>{calls.push({action,body});return research?research(action,body):action==='load'?initial:action==='save'?{...body.state,revision:body.expectedRevision+1}:analysis;},openExternal:async()=>{},onEvent:listener=>{listeners.add(listener);return()=>listeners.delete(listener);}}};
 function load(file){
  if(modules.has(file))return modules.get(file).exports;
  const module={exports:{}};modules.set(file,module);
  const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
  function importModule(name){
   if(name.endsWith('.css'))return {};
   if(name==='lucide-react')return new Proxy({},{get:()=>()=>null});
   if(name==='./research-graph-layout')return {RESEARCH_NODE_WIDTH:224,RESEARCH_NODE_HEIGHT:128,layoutResearchGraph:(analysis,signal)=>{layouts.push({analysis,signal});return layout(analysis,signal);}};
   if(name==='@xyflow/react')return {Position:{Right:'right',Left:'left'},MarkerType:{ArrowClosed:'arrowclosed'},BaseEdge:()=>null,Background:()=>null,Controls:()=>null,ReactFlow:props=>{
    if(graphThrows())throw Error('Fixture graph render failure');
    assert.ok(props.nodes.every(node=>Number.isFinite(node.position?.x)&&Number.isFinite(node.position?.y)),'No node can use a missing/stale position');
    return React.createElement('div',{'data-testid':'graph'},props.nodes.map(node=>React.createElement('button',{key:node.id,onClick:()=>props.onNodeClick({},node)},node.data.label)),props.children);
   }};
   if(name==='../i18n')return {useT:()=>({lang:'zh',t:(value,vars={})=>value.replace(/\{(\w+)\}/g,(_,key)=>vars[key]??'')})};
   if(name.startsWith('.'))return load(path.resolve(path.dirname(file),name+'.ts'));
   return require(name);
  }
  new Function('require','module','exports',code)(importModule,module,module.exports);return module.exports;
 }
 const View=load(path.join(root,'ResearchPlanner.tsx')).default;
 const props={project:project(),incoming,onHandoff:handoff,onDirtyChange:value=>dirty.push(value)};
 const wrap=override=>React.createElement(View,{...props,...override});
 return {wrap,calls,dirty,layouts,emit:event=>listeners.forEach(listener=>listener(event)),data:load(path.join(root,'research-data.ts'))};
}
const button=(view,label)=>view.root.findAllByType('button').find(item=>text(item).trim()===label);
const click=async(view,label)=>{const item=button(view,label);assert.ok(item,label);assert.notEqual(item.props.disabled,true,label);await act(async()=>{void item.props.onClick();await settle();});};
const change=async(view,id,value)=>act(async()=>{view.root.findByProps({id}).props.onChange({target:{value}});await settle();});

test('evidence is explicitly collected, deduplicated, saved by revision, and drafts survive tab changes',async()=>{
 const h=fixture(null,{incoming:[evidence('other',{doi:'https://doi.org/10.1000/E1'}),evidence('e2')]}),view=await actCreate(h.wrap());
 assert.deepEqual(h.calls.map(call=>call.action),['load']);
 await click(view,'收录当前问答结果');
 await change(view,'research-topic','A revised topic');await change(view,'research-note-title','Unadded note');
 await act(async()=>{view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});
 assert.equal(view.root.findByProps({id:'research-topic'}).props.value,'A revised topic');assert.equal(view.root.findByProps({id:'research-note-title'}).props.value,'Unadded note');
 await click(view,'保存研究内容');const save=h.calls.find(call=>call.action==='save');
 assert.equal(save.body.expectedRevision,4);assert.deepEqual(save.body.state.evidence.map(item=>item.id),['e1','e2']);assert.equal(save.body.state.topic,'A revised topic');
 assert.equal(h.dirty.at(-1),true,'Unadded notes must remain visibly unsaved');
 assert.equal(h.calls.filter(call=>call.action==='generate').length,0);
 await act(async()=>view.unmount());
});

test('cancelled generation cannot publish, save, or release a newer save lock',async()=>{
 const generation=deferred(),save=deferred();const h=fixture((action,body)=>action==='load'?saved():action==='generate'?generation.promise:action==='save'?save.promise:{}),view=await actCreate(h.wrap());
 await change(view,'research-topic','New question');await click(view,'生成图谱与建议');await click(view,'取消更新');await click(view,'保存研究内容');
 await act(async()=>{generation.reject(Error('RESEARCH_CANCELLED'));await settle();});
 assert.ok(button(view,'正在保存…'));assert.equal(button(view,'正在保存…').props.disabled,true);
 assert.equal(h.calls.filter(call=>call.action==='save').length,1);
 await act(async()=>{save.resolve(saved({topic:'New question',revision:5}));await settle();});assert.ok(button(view,'保存研究内容'));assert.equal(h.dirty.at(-1),false);
 await act(async()=>view.unmount());
});

test('late loads and generation from a previous project never replace the current project',async()=>{
 const firstLoad=deferred(),generation=deferred();const h=fixture((action,body)=>action==='load'?body.project==='C:/ProjectA'?firstLoad.promise:saved({topic:body.project}):action==='generate'?generation.promise:{}),view=await actCreate(h.wrap());
 await act(async()=>{view.update(h.wrap({project:project('B')}));await settle();});
 await act(async()=>{firstLoad.resolve(saved({topic:'Wrong old project'}));await settle();});assert.equal(view.root.findByProps({id:'research-topic'}).props.value,'C:/ProjectB');
 await click(view,'生成图谱与建议');await act(async()=>{view.update(h.wrap({project:project('C')}));await settle();});
 assert.equal(h.calls.filter(call=>call.action==='cancel').length,1);
 await act(async()=>{generation.resolve(analysis);await settle();});assert.equal(view.root.findByProps({id:'research-topic'}).props.value,'C:/ProjectC');assert.equal(h.calls.filter(call=>call.action==='save').length,0);
 await act(async()=>view.unmount());
});

test('generated analysis remains a local draft when optimistic save conflicts',async()=>{
 const h=fixture((action,body)=>action==='load'?saved():action==='generate'?analysis:Promise.reject(Error('RESEARCH_CONFLICT'))),view=await actCreate(h.wrap());
 await click(view,'生成图谱与建议');assert.match(text(view.toJSON()),/其他窗口已更新此项目/);assert.match(text(view.toJSON()),/Research plan/);assert.equal(h.dirty.at(-1),true);
 await click(view,'保存研究内容');const saves=h.calls.filter(call=>call.action==='save');assert.equal(saves.length,2);assert.equal(saves[1].body.expectedRevision,4);assert.deepEqual(saves[1].body.state.analysis,analysis);
 await act(async()=>view.unmount());
});

test('handoff is single-flight, failed handoff can retry, and completed handoff cannot repeat',async()=>{
 const first=deferred(),second=deferred();let calls=0;const h=fixture(null,{initial:saved({analysis}),handoff:()=>++calls===1?first.promise:second.promise}),view=await actCreate(h.wrap());
 await act(async()=>{view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});
 const submit=button(view,'交给主对话');await act(async()=>{void submit.props.onClick();void submit.props.onClick();await settle();});assert.equal(calls,1);assert.ok(button(view,'正在交接…'));
 await act(async()=>{first.reject(Error('offline'));await settle();});assert.match(text(view.toJSON()),/未能交给主对话/);
 await click(view,'交给主对话');assert.equal(calls,2);await act(async()=>{second.resolve();await settle();});assert.equal(button(view,'已交给主对话').props.disabled,true);
 await act(async()=>view.unmount());
});

test('removing a source clears unsupported analysis and marks the new evidence set unsaved',async()=>{
 const h=fixture(null,{initial:saved({analysis})}),view=await actCreate(h.wrap());
 await act(async()=>{view.root.findByProps({'aria-label':'移除资料：Paper e1'}).props.onClick();await settle();});
 assert.doesNotMatch(text(view.toJSON()),/Research plan/);assert.equal(h.dirty.at(-1),true);await click(view,'保存研究内容');const next=h.calls.find(call=>call.action==='save').body.state;assert.equal(next.analysis,null);assert.equal(next.analysisStale,true);assert.deepEqual(next.evidence,[]);
 await act(async()=>view.unmount());
});

test('normalization bounds evidence, avoids id collisions, and never opens unsafe source URLs',()=>{
 const {data}=fixture();const initial=Array.from({length:79},(_,i)=>evidence('p'+i));
 const result=data.mergeResearchEvidence(initial,[evidence('p0',{doi:'10.1000/new',detail:'a'.repeat(9000)}),evidence('extra')]);
 assert.equal(result.evidence.length,80);assert.equal(result.omitted,1);assert.equal(new Set(result.evidence.map(item=>item.id)).size,80);assert.equal(result.evidence.at(-1).detail.length,8000);
 assert.equal(data.mergeResearchEvidence(result.evidence,[evidence('same',{doi:'https://doi.org/10.1000/NEW'})]).duplicates,1);
 assert.equal(data.researchSourceUrl(evidence('u',{url:'javascript:alert(1)'})),null);assert.equal(data.researchSourceUrl(evidence('u',{url:'https://user:secret@example.org'})),null);assert.equal(data.researchSourceUrl(evidence()),'https://doi.org/10.1000/e1');
});

async function actCreate(element){let view;await act(async()=>{view=create(element);await settle();});return view;}

test('source management starts collapsed with or without sources; result views share one load and show different guidance',async()=>{
 for(const evidenceSet of [[],[evidence()]]){
  const h=fixture(null,{initial:saved({evidence:evidenceSet})}),view=await actCreate(h.wrap());
  assert.notEqual(view.root.findByProps({className:'research-materials'}).props.open,true);
  assert.match(text(view.root.findByProps({role:'tabpanel'})),/让研究线索连成图谱/);
  await act(async()=>{view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});
  assert.match(text(view.root.findByProps({role:'tabpanel'})),/把研究问题变成下一步行动/);
  assert.equal(view.root.findByProps({id:'research-tab-suggestions'}).props['aria-selected'],true);
  assert.deepEqual(h.calls.map(call=>call.action),['load']);
  await act(async()=>view.unmount());
 }
});

test('one generation supplies both result views without reloading, resetting edits or making another model call',async()=>{
 const h=fixture(),view=await actCreate(h.wrap());
 await click(view,'生成图谱与建议');
 await change(view,'research-topic','Unsaved refined topic');
 await change(view,'research-note-title','Keep this note draft');
 await act(async()=>{view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});
 assert.match(text(view.root.findByProps({role:'tabpanel'})),/Validate the hypothesis/);
 assert.equal(button(view,'交给主对话').props.disabled,true,'Stale suggestions remain blocked after a topic edit');
 await act(async()=>{view.root.findByProps({id:'research-tab-graph'}).props.onClick();await settle();});
 assert.equal(view.root.findByProps({id:'research-topic'}).props.value,'Unsaved refined topic');
 assert.equal(view.root.findByProps({id:'research-note-title'}).props.value,'Keep this note draft');
 assert.deepEqual(h.calls.map(call=>call.action),['load','generate','save']);
 await act(async()=>view.unmount());
});

test('updating a rendered map with new node IDs waits for its own layout without passing stale positions',async()=>{
 const next={...analysis,nodes:analysis.nodes.map(node=>({...node,id:'new-'+node.id}))},pending=deferred();
 const h=fixture((action,body)=>action==='load'?saved({analysis}):action==='generate'?next:{...body.state,revision:5},{layout:value=>value===analysis?Promise.resolve(simpleLayout(value)):pending.promise});
 const view=await actCreate(h.wrap());assert.equal(view.root.findAllByProps({'data-testid':'graph'}).length,1);
 await click(view,'更新图谱与建议');
 assert.equal(view.root.findAllByProps({'data-testid':'graph'}).length,0);
 assert.match(text(view.toJSON()),/正在整理图谱布局/);assert.equal(h.layouts[0].signal.aborted,true);
 await act(async()=>{pending.resolve(simpleLayout(next));await settle();});
 assert.equal(view.root.findAllByProps({'data-testid':'graph'}).length,1);assert.doesNotMatch(text(view.toJSON()),/图谱布局未能完成/);
 await act(async()=>view.unmount());assert.equal(h.layouts.at(-1).signal.aborted,true);
});

test('late layout results cannot overwrite a changed project, and layout rejection can retry without losing research',async()=>{
 const old=deferred(),fresh=deferred();let attempt=0;
 const h=fixture((action,body)=>action==='load'?saved({analysis,topic:body.project}):{}, {layout:()=>++attempt===1?old.promise:attempt===2?fresh.promise:Promise.resolve(simpleLayout(analysis))});
 const view=await actCreate(h.wrap());await act(async()=>{view.update(h.wrap({project:project('B')}));await settle();});
 assert.equal(h.layouts[0].signal.aborted,true);
 await act(async()=>{old.resolve(simpleLayout(analysis));await settle();});assert.match(text(view.toJSON()),/正在整理图谱布局/);
 await act(async()=>{fresh.reject(Error('Worker failed'));await settle();});assert.match(text(view.toJSON()),/图谱布局未能完成/);
 assert.equal(view.root.findByProps({id:'research-topic'}).props.value,'C:/ProjectB');
 await act(async()=>{view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});assert.match(text(view.toJSON()),/Validate the hypothesis/);
 await act(async()=>{view.root.findByProps({id:'research-tab-graph'}).props.onClick();await settle();});await click(view,'重新布局');
 assert.equal(view.root.findAllByProps({'data-testid':'graph'}).length,1);assert.equal(h.calls.filter(call=>call.action==='generate').length,0);
 await act(async()=>view.unmount());
});

test('a graph rendering exception stays inside the graph and can be retried',async()=>{
 let fail=true;const h=fixture(null,{initial:saved({analysis}),graphThrows:()=>fail});
 const view=await actCreate(h.wrap());assert.match(text(view.toJSON()),/图谱布局未能完成/);
 assert.equal(view.root.findByProps({id:'research-topic'}).props.value,'Research question');
 fail=false;await click(view,'重新布局');assert.equal(view.root.findAllByProps({'data-testid':'graph'}).length,1);
 await act(async()=>view.unmount());
});

test('progress belongs only to the active generation and cannot revive a cancelled request',async()=>{
 const pending=deferred();const h=fixture((action)=>action==='load'?saved():action==='generate'?pending.promise:{}),view=await actCreate(h.wrap());
 await click(view,'生成图谱与建议');const id=h.calls.find(call=>call.action==='generate').body.requestId;
 await act(async()=>{h.emit({type:'research-progress',requestId:'old',phase:'validating',receivedChars:9999});await settle();});
 assert.match(text(view.toJSON()),/正在连接模型服务/);assert.doesNotMatch(text(view.toJSON()),/9,999/);
 await act(async()=>{h.emit({type:'research-progress',requestId:id,phase:'generating',receivedChars:1234});await settle();});
 assert.match(text(view.toJSON()),/正在接收研究内容/);assert.match(text(view.toJSON()),/1,234/);
 await click(view,'取消更新');
 await act(async()=>{h.emit({type:'research-progress',requestId:id,phase:'validating',receivedChars:9999});pending.resolve(analysis);await settle();});
 assert.doesNotMatch(text(view.toJSON()),/正在校验研究内容/);assert.equal(h.calls.filter(call=>call.action==='save').length,0);
 await act(async()=>view.unmount());
});

test('an old agent asks for a restart without exposing a raw handoff error or losing the suggestion',async()=>{
 const h=fixture(null,{initial:saved({analysis}),handoff:async()=>{throw Error('RESEARCH_HANDOFF_NOT_READY');}}),view=await actCreate(h.wrap());
 await act(async()=>{view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});
 await click(view,'交给主对话');assert.match(text(view.toJSON()),/请重启智能体后再交给主对话/);
 assert.doesNotMatch(text(view.toJSON()),/RESEARCH_HANDOFF_NOT_READY/);assert.ok(button(view,'交给主对话'));assert.match(text(view.toJSON()),/Validate the hypothesis/);
 await act(async()=>view.unmount());
});
