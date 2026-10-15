import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';

// This renderer runs React effects without a browser, user profile or real SSH calls.
const root=fileURLToPath(new URL('../',import.meta.url));
const testRequire=createRequire(path.join(root,'package.json'));
const React=testRequire('react');
const {act,create}=testRequire('react-test-renderer');
const Context=React.createContext(null);
const modules=new Map();
let clock=0,nextTimer=0,timers=new Map();
const schedule=(fn,ms,repeat)=>{const id=++nextTimer;timers.set(id,{fn,at:clock+ms,ms,repeat});return id;};
const clear=id=>timers.delete(id);
function load(file){
 if(modules.has(file))return modules.get(file).exports;
 const module={exports:{}};modules.set(file,module);
 const source=fs.readFileSync(file,'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 const require=name=>{
  if(name==='react')return React;
  if(name==='react/jsx-runtime')return testRequire(name);
  if(name==='lucide-react')return new Proxy({},{get:(_,key)=>key==='__esModule'?false:()=>null});
  if(name==='@xyflow/react')return {ReactFlow:()=>null,Background:()=>null,Controls:()=>null};
  if(name.endsWith('.css'))return {};
  if(name==='../store')return {useApp:()=>React.useContext(Context)};
  if(name.startsWith('.')){
   const p=path.resolve(path.dirname(file),name);
   if(p.endsWith('.json'))return JSON.parse(fs.readFileSync(p,'utf8'));
   return load(['', '.ts','.tsx'].map(ext=>p+ext).find(f=>fs.existsSync(f)));
  }
  throw Error('Unmocked import: '+name);
 };
 new Function('require','exports','module','setTimeout','clearTimeout','setInterval','clearInterval',code)(require,module.exports,module,(fn,ms)=>schedule(fn,ms,false),clear,(fn,ms)=>schedule(fn,ms,true),clear);
 return module.exports;
}
const Panel=load(path.join(root,'src/renderer/src/views/WorkflowPanel.tsx')).default;
const catalog=JSON.parse(fs.readFileSync(path.join(root,'resources/workflows/catalog.json'),'utf8'));
const {workflowReadinessKey}=load(path.join(root,'src/renderer/src/views/workflowReadinessKey.ts'));
const store=()=>{const data=new Map();return {getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,String(v)),removeItem:k=>data.delete(k)};};
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
async function advance(ms){
 const until=clock+ms;
 while(true){const next=[...timers].filter(([,t])=>t.at<=until).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;
  const [id,t]=next;clock=t.at;if(t.repeat)t.at+=t.ms;else timers.delete(id);
  await act(async()=>{t.fn();await settle();});
 }
 clock=until;
}
function setup(handler=()=>undefined){
 clock=0;timers=new Map();globalThis.localStorage=store();globalThis.sessionStorage=store();
 const calls=[];
 const hosts=[{id:'a',name:'175',username:'test',hostKey:'trusted'},{id:'b',name:'176',username:'test',hostKey:'trusted'}];
 const api={sshHosts:async()=>hosts,personalCompute:async(host,action,body={})=>{
  calls.push({host,action,body});const custom=handler(host,action,body);if(custom!==undefined)return custom;
  if(action==='workflow_catalog')return new Promise(()=>{});
  if(action==='workflow_inbox')return null;
  if(action==='status')return {sources:{shared:'/isolated/test'},images:[]};
  if(action==='workflow_plans')return [];
  if(action==='workflow_prepare_status')return {state:'idle',detail:'尚未配置'};
  if(action==='workflow_readiness')return {steps:{}};
  throw Error('Unexpected action '+action);
 },dshRpc:async()=>({}),dshNewSession:async()=> 'isolated-session'};
 globalThis.window={biodsh:api};
 const state={dsh:{state:'running'},currentSession:'test-session',setCurrentSession:()=>{},setTab:()=>{}};
 let renderer;
 const tree=active=>React.createElement(Context.Provider,{value:state},React.createElement(Panel,{active}));
 return {calls,api,state,
  async mount(active=true){await act(async()=>{renderer=create(tree(active));await settle();});return renderer;},
  async show(active){await act(async()=>{renderer.update(tree(active));await settle();});},
  async stop(){await act(async()=>renderer.unmount());assert.equal(timers.size,0);},
  get view(){return renderer.root;},
  count(action){return calls.filter(c=>c.action===action).length;},
 };
}
const text=node=>node.children.map(c=>typeof c==='string'?c:text(c)).join('');
const button=(s,label)=>s.view.findAllByType('button').find(b=>text(b)===label);
async function click(b){assert.ok(b,'button exists');await act(async()=>{b.props.onClick();await settle();});}
const selected=s=>s.view.findAllByType('input').filter(i=>i.props.type==='checkbox'&&i.props.checked).map(i=>i.props['aria-label']);
const namedInput=(s,label)=>s.view.findAllByType('label').find(l=>l.children[0]===label).findByType('input');
async function change(input,value){await act(async()=>{input.props.onChange({target:{value}});await settle();});}
async function choose(s,id='rnaseq'){
 const flow=catalog.workflows.find(f=>f.id===id);
 const article=s.view.findAllByType('article').find(a=>a.findAllByType('h2').some(h=>text(h)===flow.name));
 await click(article.findByType('button'));return flow;
}

test('local workflow cards appear while bridge catalog and host responses are pending',async()=>{
 const s=setup();s.api.sshHosts=()=>new Promise(()=>{});await s.mount(false);
 assert.equal(s.view.findAllByType('h2').length,0);
 await s.show(true);
 assert.equal(s.view.findAllByType('article').length,catalog.workflows.length);
 assert.equal(s.count('workflow_catalog'),0);
 await s.show(false);await s.show(true);
 assert.equal(s.view.findAllByType('article').length,catalog.workflows.length);
 await s.stop();
});

test('500 conversation round trips retain steps, parameters, input, result name and resources without rechecking software',async()=>{
 const s=setup();await s.mount();await choose(s);
 await click(s.view.findAllByType('button').find(b=>b.props['aria-label']==='比较表达差异'));
 const padj=s.view.findAllByType('input').find(i=>i.props['aria-label']==='校正后的显著性上限');
 assert.ok(padj,'translated significance parameter');await change(padj,'0.01');
 await change(namedInput(s,'本次数据路径'),'/isolated/my-data');await change(namedInput(s,'结果名称'),'my_result');
 await change(namedInput(s,'使用多少核 CPU'),'12');
 const before=selected(s),checks=s.count('workflow_readiness');
 for(let i=0;i<500;i++){await s.show(false);await advance(6000);await s.show(true);}
 assert.deepEqual(selected(s),before);assert.equal(namedInput(s,'本次数据路径').props.value,'/isolated/my-data');
 assert.equal(namedInput(s,'结果名称').props.value,'my_result');assert.equal(namedInput(s,'使用多少核 CPU').props.value,12);
 assert.equal(Number(s.view.findAllByType('input').find(i=>i.props['aria-label']==='校正后的显著性上限').props.value),0.01);
 assert.equal(s.count('workflow_readiness'),checks);assert.equal(s.count('workflow_prepare'),0);
 await s.stop();
});

test('cached readiness survives returning to a workflow; manual check and changed software/server still recheck',async()=>{
 const s=setup();await s.mount();await choose(s);const count=s.count('workflow_readiness');
 await click(button(s,'全部流程'));await choose(s);assert.equal(s.count('workflow_readiness'),count);
 await click(button(s,'检查步骤环境'));assert.equal(s.count('workflow_readiness'),count+1);
 const server=s.view.findAllByType('select').find(n=>n.props.value==='a'&&n.findAllByType('option').some(o=>o.props.value==='b'));
 await change(server,'b');assert.equal(s.count('workflow_readiness'),count+2);
 await s.stop();
});

test('hidden idle page stops server polling and returning does not erase content after an SSH failure',async()=>{
 let offline=false;const s=setup((h,a)=>offline&&a==='status'?Promise.reject(Error('服务器暂时无法连接')):undefined);
 await s.mount();await choose(s);await s.show(false);const count=s.calls.length;
 await advance(30000);assert.equal(s.calls.length,count);
 offline=true;await s.show(true);assert.ok(s.view.findAllByProps({role:'alert'}).some(a=>text(a).includes('服务器暂时无法连接')));
 assert.ok(s.view.findAllByType('article').length>0);await s.stop();
});

test('a slow server request is not multiplied by the polling interval',async()=>{
 let release;const s=setup((h,a)=>a==='status'?new Promise(r=>release=r):undefined);
 await s.mount();await choose(s);await advance(30000);assert.equal(s.count('status'),1);
 await act(async()=>{release({sources:{},images:[]});await settle();});await advance(5000);
 assert.equal(s.count('status'),2);await s.stop();
});

test('returning repeatedly while the server is slow shares one refresh request',async()=>{
 let release;const s=setup((h,a)=>a==='status'?new Promise(r=>release=r):undefined);
 await s.mount();await choose(s);
 for(let i=0;i<10;i++){await s.show(false);await s.show(true);}
 assert.equal(s.count('status'),1);assert.equal(s.count('workflow_plans'),1);
 await act(async()=>{release({sources:{},images:[]});await settle();});await s.stop();
});

test('all nine workflows remain visible with no repeated readiness checks after conversation switches',async()=>{
 const s=setup();await s.mount();assert.equal(catalog.workflows.length,9);
 for(const flow of catalog.workflows){
  await choose(s,flow.id);const checks=s.count('workflow_readiness');
  await s.show(false);await s.show(true);
  assert.equal(s.view.findByType('h1').children[0],flow.name);assert.ok(s.view.findAllByType('article').length>0);
  assert.equal(s.count('workflow_readiness'),checks);await click(button(s,'全部流程'));
 }
 assert.equal(s.count('workflow_prepare'),0);await s.stop();
});

test('changing quantification software invalidates only the relevant readiness cache',async()=>{
 const s=setup();await s.mount();await choose(s);
 await click(s.view.findAllByType('button').find(b=>b.props['aria-label']==='汇总表达计数'));
 const mode=s.view.findAllByType('select').find(n=>n.findAllByType('option').some(o=>o.props.value==='count_only'));
 assert.ok(mode);const checks=s.count('workflow_readiness');await change(mode,'count_only');
 assert.equal(s.count('workflow_readiness'),checks+1);await s.show(false);await s.show(true);
 assert.equal(s.count('workflow_readiness'),checks+1);await s.stop();
});

test('unexpected render errors have a readable recovery button instead of a blank page',async()=>{
 let invalid=false;const s=setup((h,a)=>a==='workflow_plans'?Promise.resolve(invalid?[{plan:null}]:[]):undefined);
 const previous=console.error;console.error=()=>{};
 try{
  await s.mount();await choose(s);invalid=true;await advance(5000);
  assert.ok(s.view.findAllByProps({role:'alert'}).some(a=>text(a).includes('流程页面显示失败')));
  invalid=false;await click(button(s,'重新载入流程'));
  assert.equal(s.view.findByType('h1').children[0],catalog.workflows.find(f=>f.id==='rnaseq').name);
  await s.stop();
 }finally{console.error=previous;}
});

test('model-created plan inbox is checked on return without resetting this workflow editor',async()=>{
 let inbox=null;const s=setup((h,a)=>a==='workflow_inbox'?Promise.resolve(inbox):undefined);
 await s.mount();await choose(s);await change(namedInput(s,'结果名称'),'keep_me');await s.show(false);
 inbox={host:'a',workflow:'rnaseq',id:'model-plan',context:{sessionId:'test-session'}};await s.show(true);
 assert.equal(localStorage.getItem('omaax.workflow.inbox-seen'),'model-plan');
 assert.equal(namedInput(s,'结果名称').props.value,'keep_me');await s.stop();
});

test('a stale plan notification from another conversation cannot change the selected workflow',async()=>{
 let inbox=null;const s=setup((h,a)=>a==='workflow_inbox'?Promise.resolve(inbox):undefined);
 await s.mount();await choose(s);await change(namedInput(s,'结果名称'),'this_conversation');await s.show(false);
 inbox={host:'b',workflow:'gene_family',id:'old-plan',context:{sessionId:'other-session'}};
 await s.show(true);assert.equal(s.view.findByType('h1').children[0],catalog.workflows.find(f=>f.id==='rnaseq').name);
 assert.equal(namedInput(s,'结果名称').props.value,'this_conversation');
 assert.notEqual(localStorage.getItem('omaax.workflow.inbox-seen'),'old-plan');await s.stop();
});

test('missing configuration provides an inline model handoff and never starts environment installation',async()=>{
 const s=setup((h,a)=>a==='workflow_validate'?Promise.reject(Error('检查测序质量需要填写：样本与分析配置文件')):undefined);
 s.api.dshRpc=async(method)=>method==='workspace.list'?{items:[{workspaceId:'test',sessionIds:['test-session']}]}:{};
 const transitions=[];s.state.setTab=tab=>transitions.push(tab);
 await s.mount();await choose(s);await click(button(s,'生成执行计划'));
 assert.ok(s.view.findAllByProps({role:'alert'}).some(a=>text(a).includes('样本与分析配置文件')));
 assert.equal(s.count('workflow_prepare'),0);assert.equal(s.count('workflow_confirm'),0);
 await click(button(s,'在对话中补齐配置'));assert.deepEqual(transitions,['chat']);await s.stop();
});

test('execution records belong to the current conversation',async()=>{
 const record=sid=>({plan:{id:sid,workflow:'rnaseq',version:catalog.version,context:{sessionId:sid},steps:['rnaseq_qc'],prefix:sid},state:'awaiting_confirmation'});
 const s=setup((h,a)=>a==='workflow_plans'?Promise.resolve([record('other-session'),record('test-session')]):undefined);
 await s.mount();await choose(s);assert.equal(s.view.findAllByType('button').filter(b=>text(b)==='查看并确认').length,1);
 await s.stop();
});

test('switching conversations clears the previous result panel and preserves the editable selection',async()=>{
 const record={plan:{id:'finished',workflow:'rnaseq',version:catalog.version,context:{sessionId:'test-session'},steps:['rnaseq_qc'],prefix:'finished'},state:'complete',progress:[]};
 const s=setup((h,a)=>a==='workflow_plans'?Promise.resolve([record]):a==='workflow_result'?Promise.resolve({id:'finished',name:'RNA',state:'complete',fileCount:0,files:[],steps:[]}):undefined);
 await s.mount();await choose(s);await change(namedInput(s,'结果名称'),'keep-editing');await click(button(s,'查看结果'));
 assert.ok(s.view.findAllByType('h2').some(h=>text(h)==='执行结果'));
 await s.show(false);await s.show(true);assert.ok(s.view.findAllByType('h2').some(h=>text(h)==='执行结果'));
 s.state.currentSession='other-session';await s.show(true);assert.equal(s.view.findAllByType('h2').some(h=>text(h)==='执行结果'),false);
 assert.equal(namedInput(s,'结果名称').props.value,'keep-editing');await s.stop();
});

test('model software choices reach the environment button without installing unselected tools',async()=>{
 let inbox=null;const s=setup((h,a,b)=>a==='workflow_inbox'?Promise.resolve(inbox):a==='workflow_prepare'?Promise.resolve({id:'only-requested',state:'complete',detail:'复用已有环境',request:b}):undefined);
 await s.mount();await choose(s,'singlecell');await s.show(false);
 inbox={host:'a',id:'model-draft',workflow:'singlecell',context:{sessionId:'test-session'},state:'needs_environment',selection:{input:'/isolated/raw-singlecell',prefix:'model-selected',steps:['singlecell_upstream','singlecell_report'],configs:{singlecell_upstream:{aligner:'star',protocol:'10XV2',skip_cellbender:true,skip_qcatch:true}},cpu:4,memoryMb:8192,timeoutSeconds:3600}};
 await s.show(true);assert.equal(s.count('workflow_prepare'),0);
 assert.equal(namedInput(s,'本次数据路径').props.value,'/isolated/raw-singlecell');assert.equal(namedInput(s,'结果名称').props.value,'model-selected');
 assert.equal(namedInput(s,'使用多少核 CPU').props.value,4);assert.equal(namedInput(s,'内存上限（GiB）').props.value,8);
 await click(button(s,'检查并配置所选步骤'));
 const request=s.calls.find(c=>c.action==='workflow_prepare').body;
 assert.deepEqual(request.steps,['singlecell_upstream','singlecell_report']);assert.equal(request.configs.singlecell_upstream.aligner,'star');assert.equal(request.configs.singlecell_upstream.skip_cellbender,true);
 assert.equal(Object.keys(request.configs).length,2);
 await change(namedInput(s,'结果名称'),'my-later-edit');await s.show(false);await s.show(true);assert.equal(namedInput(s,'结果名称').props.value,'my-later-edit');await s.stop();
});

test('reopening the application restores the last model selection even if its notice was already seen',async()=>{
 const inbox={host:'a',id:'saved-model-draft',workflow:'singlecell',context:{sessionId:'test-session'},state:'needs_environment',selection:{input:'/isolated/raw',prefix:'saved-draft',steps:['singlecell_upstream'],configs:{singlecell_upstream:{aligner:'star'}},cpu:4,memoryMb:8192,timeoutSeconds:3600}};
 const s=setup((h,a)=>a==='workflow_inbox'?Promise.resolve(inbox):undefined);
 localStorage.setItem('omaax.workflow.inbox-seen',inbox.id);await s.mount();
 assert.equal(s.view.findByType('h1').children[0],catalog.workflows.find(f=>f.id==='singlecell').name);
 assert.equal(namedInput(s,'结果名称').props.value,'saved-draft');assert.equal(namedInput(s,'本次数据路径').props.value,'/isolated/raw');await s.stop();
});

test('an environment task completes and generates exactly one plan while the page is hidden',async()=>{
 let task={state:'idle',detail:'尚未配置'};let saved=[];
 const s=setup((h,a,b)=>{
  if(a==='workflow_validate')return Promise.resolve({valid:true});
  if(a==='workflow_prepare'){task={id:'env-1',state:'running',detail:'安装中',request:{steps:b.steps,parameters:b.parameters,configs:b.configs}};return Promise.resolve(task);}
  if(a==='workflow_prepare_status')return Promise.resolve(task);
  if(a==='workflow_plans')return Promise.resolve(saved);
  if(a==='workflow_plan'){const p={plan:{...b,id:'new-plan',name:'RNA-seq',version:catalog.version,inputs:{},resources:{cpu:b.cpu,memoryMb:b.memoryMb,timeoutSeconds:b.timeoutSeconds}},digest:'digest',state:'awaiting_confirmation',confirmed:false};saved=[p];return Promise.resolve(p);}
 });
 s.api.dshRpc=async()=>({projectId:'test',projectName:'测试',sessionId:'test-session',sessionName:'测试'});
 await s.mount();await choose(s);await click(button(s,'生成执行计划'));assert.equal(s.count('workflow_prepare'),1);
 await s.show(false);task={...task,state:'complete',detail:'环境准备完成'};await advance(12000);
 assert.equal(s.count('workflow_plan'),1);assert.equal(s.count('workflow_confirm'),0);
 await s.show(true);assert.ok(button(s,'查看并确认'));assert.equal(s.count('workflow_prepare'),1);await s.stop();
});

test('App always retains the workflow panel instead of conditionally mounting it',()=>{
 const app=fs.readFileSync(path.join(root,'src/renderer/src/App.tsx'),'utf8');
 assert.match(app,/<WorkflowPanel active=\{tab === 'workflows'\}\/>/);
 assert.doesNotMatch(app,/tab === 'workflows' &&/);
});

test('readiness signatures cover every declared upstream software route across all workflows',()=>{
 let options=0;
 for(const flow of catalog.workflows){
  const original=workflowReadinessKey('a',flow,{},{});
  for(const step of [...flow.nodes,...flow.extraNodes].filter(s=>s.nextflowSource)){
   for(const [key,schema] of Object.entries(step.configSchema?.properties||{})){
    if(!['aligner','peakcaller','profile','technology','refine_bins_dastool','bbnorm'].includes(key)&&!/^(run_|skip_|use_|perform_)/.test(key))continue;
    const value=step.configDefaults?.[key]??schema.default;
    assert.notEqual(workflowReadinessKey('a',flow,{},{[step.id]:{[key]:typeof value==='boolean'?!value:'different-route'}}),original,flow.id+'/'+step.id+'/'+key);options++;
   }
  }
 }
 assert.ok(options>50,options+' route options checked');
});

test('ordinary threshold/path edits reuse checks; annotation and single-cell software choices do not',()=>{
 const flow=catalog.workflows.find(f=>f.id==='rnaseq'),key=workflowReadinessKey('a',flow,{},{});
 assert.equal(workflowReadinessKey('a',flow,{rnaseq_de:{padj:0.01},rnaseq_align:{hisat2_index:'/reference/index'}},{}),key);
 for(const option of ['prefer_bioc_gene2go','skip_go','skip_kegg'])assert.notEqual(workflowReadinessKey('a',flow,{annotation:{[option]:true}},{}),key);
 assert.notEqual(workflowReadinessKey('a',flow,{},{singlecell_analyze:{integration:'harmony'}}),key);
 assert.notEqual(workflowReadinessKey('a',flow,{},{singlecell_analyze:{doublets:{enabled:true}}}),key);
 assert.equal(workflowReadinessKey('a',flow,{},{singlecell_analyze:{doublets:{threshold:0.1}}}),key);
});


test('a model plan changing workflow while the previous diagram exists never crashes',async()=>{
 let inbox=null;const s=setup((h,a)=>a==='workflow_inbox'?Promise.resolve(inbox):undefined);
 await s.mount();await choose(s,'atacseq');await s.show(false);
 inbox={host:'a',id:'singlecell-model-final',workflow:'singlecell',context:{sessionId:'test-session'},state:'planned',selection:{steps:['singlecell_upstream','singlecell_report'],configs:{singlecell_upstream:{aligner:'star'}},input:'/isolated/raw',prefix:'sc-final'}};
 await s.show(true);assert.equal(s.view.findByType('h1').children[0],catalog.workflows.find(f=>f.id==='singlecell').name);
 assert.equal(namedInput(s,'结果名称').props.value,'sc-final');assert.deepEqual(selected(s),['运行可选测序上游','运行汇总图表与分析报告']);await s.stop();
});


test('result pages append without duplicates or starting analysis',async()=>{
 const record={plan:{id:'many-results',workflow:'rnaseq',version:catalog.version,context:{sessionId:'test-session'},steps:['rnaseq_qc'],prefix:'many-results'},state:'complete',progress:[]};
 const s=setup((h,a,b)=>a==='workflow_plans'?Promise.resolve([record]):a==='workflow_result'?Promise.resolve({id:'many-results',state:'complete',steps:[],fileCount:3,nextOffset:b.offset==null?2:null,files:(b.offset==null?['report.html','plot.png']:['table.tsv']).map(path=>({path,size:10,sha256:'test'}))}):undefined);
 await s.mount();await choose(s);await click(button(s,'查看结果'));await click(button(s,'加载更多结果'));
 assert.equal(s.view.findAllByType('button').filter(b=>text(b)==='下载').length,3);assert.equal(s.view.findAllByType('button').some(b=>text(b)==='加载更多结果'),false);
 assert.equal(s.calls.filter(c=>c.action==='workflow_result').at(-1).body.offset,2);assert.equal(s.count('workflow_confirm'),0);await s.stop();
});
