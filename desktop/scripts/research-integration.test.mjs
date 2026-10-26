import {test} from 'node:test';
import assert from 'node:assert/strict';
import {act,create,settle,text,fixture,assertEnglish} from './locale-test-utils.mjs';

const projects=[
 {workspaceId:'p1',path:'C:/Research One',title:'Research one',sessionIds:['s1']},
 {workspaceId:'p2',path:'C:/Research Two',title:'Research two',sessionIds:['s2']},
];
const blank=()=>({version:1,revision:0,topic:'',evidence:[],analysis:null,analysisStale:false,updatedAt:''});
const recommendation={id:'next-1',title:'Check expression',reason:'Compare the observed data.',requirements:['Sample table'],steps:['Inspect existing results'],evidenceIds:['paper'],limitations:'Needs validation.'};
const saved=()=>({...blank(),topic:'Rice study',evidence:[{id:'paper',kind:'paper',title:'Rice evidence',detail:'Source excerpt',doi:'10.1234/rice'}],analysis:{title:'Rice plan',summary:'A plan to verify.',nodes:[],edges:[],suggestions:[recommendation]}});
const button=(view,label)=>view.root.findAllByType('button').find(node=>text(node).trim()===label);
const click=async(view,label)=>{const node=button(view,label);assert.ok(node,label);assert.ok(!node.props.disabled,`${label} enabled`);await act(async()=>{node.props.onClick();await settle();});};
const field=async(view,id,value)=>act(async()=>{view.root.findByProps({id}).props.onChange({target:{value}});await settle();});

async function hub({current='s1',state=blank,qaSessions=[],qaHistory={},overrides={}}={}){
 const calls=[],answers=[],cloudSessions=[...qaSessions];let h;
 h=fixture({mocks:{'./research-graph-layout':{
  RESEARCH_NODE_WIDTH:224,RESEARCH_NODE_HEIGHT:128,
  layoutResearchGraph:async({nodes,edges})=>({positions:Object.fromEntries(nodes.map((node,index)=>[node.id,{x:index*300,y:0}])),paths:Object.fromEntries(edges.map(edge=>[edge.id,'M 0 0 L 300 0']))}),
 }},state:{dsh:{state:'running',log:[]},currentSession:current,
  setCurrentSession:id=>{calls.push({current:id});h.app.currentSession=id;},setTab:tab=>calls.push({tab})},api:{
  dshRpc:async(method,payload)=>{calls.push({method,payload});return method==='workspace.list'?{items:projects}:method==='research.handoff'?{accepted:true}:{};},
  dshNewSession:async project=>{calls.push({newProject:project});return 's3';},
  research:async(action,body)=>{calls.push({action,body});if(action==='load')return state(body.project);if(action==='save')return {...body.state,revision:body.expectedRevision+1};return {};},
  cloudQa:async(action,body,onEvent)=>{
   calls.push({cloud:action,body});if(action==='list')return cloudSessions;
   if(action==='detail')return {messages:qaHistory[body.id]||[]};
   if(action==='create'){const value={id:'qa-session',title:'Evidence discussion'};cloudSessions.push(value);return value;}
   if(action==='chat')return new Promise((resolve,reject)=>answers.push({resolve,reject,onEvent}));
   return {ok:true};
  },...overrides,
 }});
 window.confirm=()=>true;
 const View=h.get('views/KnowledgeHubView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View,'zh'));await settle();});
 return {h,view,View,calls,answers,async update(){await act(async()=>{view.update(h.wrap(View,'zh'));await settle();});},async ask(question){await field(view,'knowledge-question',question);await act(async()=>{view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});},async close(){await act(async()=>view.unmount());}};
}

test('knowledge collection excludes partial answers and keeps model notes separate from source excerpts',()=>{
 const h=fixture(),{knowledgeEvidence}=h.get('views/knowledge-research-context.ts');
 const messages=[{role:'user',text:'Incomplete question'},{role:'assistant',text:'Partial',transient:true,blocks:[{type:'sources',items:[{title:'Partial paper'}]}]},
 {role:'user',text:'Failed question'},{role:'assistant',text:'Failed draft',process:{status:'error'}},
 {role:'user',text:'Full question'},{role:'assistant',text:'Model synthesis',process:{status:'complete'},blocks:[{type:'sources',items:[{title:'Evidence paper',snippet:'Original passage',doi:'10.1234/evidence'},{title:'Unsafe URL',url:'javascript:alert(1)'}]}]}];
 const evidence=knowledgeEvidence(messages,'qa-1','en');
 assert.deepEqual(evidence.filter(e=>e.kind==='question').map(e=>e.title),['Full question']);
 assert.equal(evidence.find(e=>e.detail==='Model synthesis').kind,'note');
 assert.equal(evidence.find(e=>e.title==='Evidence paper').detail,'Original passage');
 assert.equal(evidence.find(e=>e.title==='Unsafe URL').url,undefined);
 assert.doesNotMatch(JSON.stringify(evidence),/Partial|Failed|Incomplete/);
 assert.deepEqual(knowledgeEvidence(messages,'qa-1','en'),evidence,'deduplication IDs remain stable');
});

test('hub starts on unchanged literature QA; tabs preserve question and research drafts without generating',async()=>{
 const x=await hub({current:'s2'});
 try{
  assert.equal(x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.value,'C:/Research Two');
  assert.equal(x.calls.filter(c=>c.action).length,0);
  await field(x.view,'knowledge-question','Unsent question');await click(x.view,'课题研究');await act(async()=>{x.view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});
  await field(x.view,'research-topic','Unsent topic');
  await click(x.view,'文献问答');assert.equal(x.view.root.findByProps({id:'knowledge-question'}).props.value,'Unsent question');
  await click(x.view,'课题研究');assert.equal(x.view.root.findByProps({id:'research-topic'}).props.value,'Unsent topic');
  assert.equal(x.calls.filter(c=>c.action==='load').length,1);
  assert.equal(x.calls.filter(c=>c.action==='generate').length,0);
  assert.equal(x.calls.filter(c=>c.cloud==='cancel').length,0);
 }finally{await x.close();}
});

test('tab switching keeps an in-flight answer alive and collecting requires an explicit click after completion',async()=>{
 const x=await hub();
 try{
  await x.ask('Rice evidence?');assert.equal(x.answers.length,1);await click(x.view,'课题研究');
  assert.equal(button(x.view,'收录当前问答结果').props.disabled,true);
  assert.equal(x.calls.filter(c=>c.cloud==='cancel').length,0);
  await act(async()=>{x.answers[0].resolve({answer:'Model synthesis',sources:[{title:'Rice paper',snippet:'Original rice evidence',doi:'10.1234/rice'}]});await settle();});
  assert.equal(button(x.view,'收录当前问答结果').props.disabled,false);
  assert.match(text(x.view.toJSON()),/已收录 0 条资料/);
  await click(x.view,'收录当前问答结果');await click(x.view,'保存研究内容');
  const record=x.calls.find(c=>c.action==='save').body.state;
  assert.deepEqual(record.evidence.map(e=>e.kind),['question','note','paper']);
  assert.equal(record.evidence[2].detail,'Original rice evidence');
  await click(x.view,'文献问答');assert.match(text(x.view.toJSON()),/Model synthesis/);
  await x.ask('Unfinished question');await click(x.view,'中止');await click(x.view,'课题研究');
  await click(x.view,'收录当前问答结果');assert.match(text(x.view.toJSON()),/已收录 3 条资料/);
  assert.doesNotMatch(text(x.view.root.findByProps({'aria-label':'课题研究'})),/Unfinished question/);
 }finally{await x.close();}
});

test('unsaved draft rejects a project switch until the user explicitly discards it',async()=>{
 const x=await hub();
 try{
  await click(x.view,'课题研究');await act(async()=>{x.view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});await field(x.view,'research-topic','Unsaved rice study');
  window.confirm=()=>false;
  await act(async()=>{x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.onChange({target:{value:'C:/Research Two'}});await settle();});
  assert.equal(x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.value,'C:/Research One');
  assert.equal(x.view.root.findByProps({id:'research-topic'}).props.value,'Unsaved rice study');
  window.confirm=()=>true;
  await act(async()=>{x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.onChange({target:{value:'C:/Research Two'}});await settle();});
  assert.equal(x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.value,'C:/Research Two');
  assert.equal(x.view.root.findByProps({id:'research-topic'}).props.value,'');
  assert.equal(x.calls.filter(c=>c.action==='load').at(-1).body.project,'C:/Research Two');
 }finally{await x.close();}
});

const qaSessions=[{id:'qa-rice',title:'Rice Q&A'},{id:'qa-mouse',title:'Mouse Q&A'}];
const qaHistory={
 'qa-rice':[{role:'user',text:'Rice question?'},{role:'assistant',text:'Rice answer',blocks:[{type:'sources',items:[{title:'Rice paper',doi:'10.1234/rice'}]}]}],
 'qa-mouse':[{role:'user',text:'Mouse question?'},{role:'assistant',text:'Mouse answer',blocks:[{type:'sources',items:[{title:'Mouse paper',doi:'10.1234/mouse'}]}]}],
};
const choose=async(view,label,value)=>act(async()=>{view.root.findByProps({'aria-label':label}).props.onChange({target:{value}});await settle();});

test('research selects Q&A independently from the destination and shares history without implicit imports',async()=>{
 const x=await hub({qaSessions,qaHistory});
 try{
  await click(x.view,'课题研究');
  assert.equal(x.calls.filter(c=>c.cloud==='list').length,1,'research reuses the mounted Q&A history list');
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.value,'');
  await choose(x.view,'选择问答来源','qa-rice');
  assert.equal(x.calls.filter(c=>c.cloud==='detail').length,1);
  assert.equal(x.view.root.findByProps({'aria-label':'知识库会话'}).props.value,'qa-rice');
  assert.equal(x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.value,'C:/Research One');
  assert.match(text(x.view.toJSON()),/所选问答有 3 条可收录资料/);
  assert.match(text(x.view.toJSON()),/已收录 0 条资料/);
  await click(x.view,'收录当前问答结果');await field(x.view,'research-topic','Keep my topic');
  await choose(x.view,'选择问答来源','qa-mouse');
  assert.equal(x.view.root.findByProps({id:'research-topic'}).props.value,'Keep my topic');
  assert.match(text(x.view.toJSON()),/已收录 3 条资料/);
  assert.doesNotMatch(text(x.view.root.findByProps({'aria-label':'课题研究'})),/Mouse question|Mouse answer/,'choosing a source alone never changes research');
  await click(x.view,'收录当前问答结果');await click(x.view,'收录当前问答结果');await click(x.view,'保存研究内容');
  const record=x.calls.find(c=>c.action==='save');
  assert.equal(record.body.project,'C:/Research One');assert.equal(record.body.state.evidence.length,6,'collection deduplicates');
  assert.ok(record.body.state.evidence.some(e=>e.title==='Rice paper'));assert.ok(record.body.state.evidence.some(e=>e.title==='Mouse paper'));
  await choose(x.view,'保存到哪个项目','C:/Research Two');
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.value,'qa-mouse');
  await click(x.view,'收录当前问答结果');await click(x.view,'保存研究内容');
  const second=x.calls.filter(c=>c.action==='save').at(-1);assert.equal(second.body.project,'C:/Research Two');assert.equal(second.body.state.evidence.length,3);
  await click(x.view,'文献问答');await choose(x.view,'知识库会话','qa-rice');await click(x.view,'课题研究');
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.value,'qa-rice');
  assert.equal(x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.value,'C:/Research Two');
  assert.equal(x.calls.filter(c=>c.action==='generate').length,0);
 }finally{await x.close();}
});

test('the chosen destination stays fixed when the main chat or Q&A selection changes',async()=>{
 const x=await hub({qaSessions,qaHistory});
 try{
  await click(x.view,'课题研究');await choose(x.view,'保存到哪个项目','C:/Research Two');
  x.h.app.currentSession='s2';await x.update();
  x.h.app.currentSession='s1';await x.update();
  assert.equal(x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.value,'C:/Research Two');
  await choose(x.view,'选择问答来源','qa-rice');
  assert.equal(x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.value,'C:/Research Two');
 }finally{await x.close();}
});

test('a pending source load cannot be replaced or collected and a failure keeps the previous source',async()=>{
 const details=[];let failMouse=true;
 const x=await hub({overrides:{cloudQa:async(action,body)=>{
  if(action==='list')return qaSessions;
  if(action==='detail'){details.push(body.id);if(body.id==='qa-rice')return {messages:qaHistory[body.id]};if(failMouse)throw Error('offline');return new Promise(resolve=>details.push(resolve));}
  return {ok:true};
 }}});
 try{
  await click(x.view,'课题研究');await choose(x.view,'选择问答来源','qa-rice');
  await choose(x.view,'选择问答来源','qa-mouse');
  assert.match(text(x.view.toJSON()),/会话加载失败，请刷新后重试/);
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.value,'qa-rice');
  failMouse=false;await choose(x.view,'选择问答来源','qa-mouse');
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.disabled,true);
  assert.equal(button(x.view,'收录当前问答结果').props.disabled,true);
  await choose(x.view,'选择问答来源','qa-rice');
  assert.equal(details.filter(value=>typeof value==='string').length,3,'busy imperative control rejects a second selection');
  await act(async()=>{details.at(-1)({messages:qaHistory['qa-mouse']});await settle();});
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.value,'qa-mouse');
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.disabled,false);
  assert.doesNotMatch(text(x.view.toJSON()),/会话加载失败，请刷新后重试/);
  assert.match(text(x.view.toJSON()),/已收录 0 条资料/);
 }finally{await x.close();}
});

test('unsaved answers block both source selectors until the answer is saved',async()=>{
 const details=[];let failSave=true;
 const x=await hub({overrides:{cloudQa:async(action,body)=>{
  if(action==='list')return qaSessions;
  if(action==='detail'){details.push(body.id);return {messages:qaHistory[body.id]||[]};}
  if(action==='chat')return {answer:'New rice answer',sources:[]};
  if(action==='message'&&body.role==='assistant'&&failSave)throw Error('offline');
  return {ok:true};
 }}});
 try{
  await choose(x.view,'知识库会话','qa-rice');await x.ask('Follow-up question');await click(x.view,'课题研究');
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.disabled,true);
  assert.equal(x.view.root.findByProps({'aria-label':'知识库会话'}).props.disabled,true);
  assert.match(text(x.view.toJSON()),/当前问答尚未保存/);
  await choose(x.view,'选择问答来源','qa-mouse');assert.deepEqual(details,['qa-rice']);
  failSave=false;await click(x.view,'文献问答');await click(x.view,'重试保存');await click(x.view,'课题研究');
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.disabled,false);
  await choose(x.view,'选择问答来源','qa-mouse');
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.value,'qa-mouse');
 }finally{await x.close();}
});

test('Q&A list refresh discards older responses and never changes the selected session',async()=>{
 const replies=[];
 const x=await hub({overrides:{cloudQa:async(action,body)=>{
  if(action==='list')return new Promise(resolve=>replies.push(resolve));
  if(action==='detail')return {messages:qaHistory[body.id]};return {ok:true};
 }}});
 try{
  await click(x.view,'课题研究');
  const refresh=x.view.root.findByProps({'aria-label':'刷新问答来源'});
  await act(async()=>{refresh.props.onClick();await settle();});
  await act(async()=>{replies[1](qaSessions);await settle();});
  await choose(x.view,'选择问答来源','qa-rice');
  await act(async()=>{replies[0]([{id:'stale',title:'STALE SESSION'}]);await settle();});
  assert.doesNotMatch(text(x.view.toJSON()),/STALE SESSION/);
  assert.equal(x.view.root.findByProps({'aria-label':'选择问答来源'}).props.value,'qa-rice');
 }finally{await x.close();}
});

test('handoff sends a readable task to the current project without internal rules or raw planning JSON',async()=>{
 const x=await hub({state:saved});
 try{
  await click(x.view,'课题研究');await act(async()=>{x.view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});await click(x.view,'交给主对话');
  assert.equal(x.calls.filter(c=>c.newProject).length,0);
  const payload=x.calls.find(c=>c.method==='research.handoff').payload;
  assert.equal(payload.sessionId,'s1');assert.equal(payload.project,'C:/Research One');assert.match(payload.requestId,/^[a-f\d-]{36}$/);
  const prompt=payload.context;
  assert.match(payload.summary,/Check expression/);assert.ok(payload.summary.length<200);assert.doesNotMatch(payload.summary,/Sample table|Source excerpt|Inspect existing results/);
  for(const required of ['Rice study','Check expression','Sample table','Inspect existing results','Needs validation.','10.1234/rice','Source excerpt'])assert.ok(prompt.includes(required),required);
  assert.doesNotMatch(prompt,/内置分析流程|原始数据保护和审计|evidenceIds|analysisStale|"topic"|"suggestion"/);
  assert.equal(x.calls.at(-1).tab,'chat');
 }finally{await x.close();}
});

test('failed cross-project handoff selects the new project session first and retries there',async()=>{
 let fail=true;const prompts=[];
 const x=await hub({state:saved,overrides:{dshRpc:async(method,payload)=>{if(method==='workspace.list')return {items:projects};if(method==='research.handoff'){prompts.push(payload);if(fail)throw Error('offline');return {accepted:true};}return {};}}});
 try{
  await click(x.view,'课题研究');await act(async()=>{x.view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});
  await act(async()=>{x.view.root.findByProps({'aria-label':'保存到哪个项目'}).props.onChange({target:{value:'C:/Research Two'}});await settle();});
  await click(x.view,'交给主对话');
  assert.equal(x.calls.find(c=>c.newProject).newProject,'p2');assert.equal(x.calls.find(c=>c.current).current,'s3');
  assert.equal(prompts[0].sessionId,'s3');assert.equal(x.calls.filter(c=>c.tab).length,0);
  assert.match(text(x.view.toJSON()),/未能交给主对话，研究建议已保留/);
  fail=false;await x.update();await click(x.view,'交给主对话');
  assert.equal(prompts[1].sessionId,'s3');assert.equal(x.calls.filter(c=>c.newProject).length,1);assert.equal(x.calls.at(-1).tab,'chat');
  assert.equal(prompts[1].requestId,prompts[0].requestId,'Uncertain acceptance retries with the same idempotency identity');
 }finally{await x.close();}
});

test('handoff keeps complete cited execution details without unrelated graph data or sources',()=>{
 const h=fixture(),{researchHandoff}=h.get('views/knowledge-research-context.ts');
 const state=saved();state.evidence.push({id:'unrelated',kind:'note',title:'Unrelated',detail:'DO_NOT_SEND'});
 state.evidence[0].detail='Parameters: threshold=0.05\n'+('Evidence '.repeat(1100))+'END_REQUIRED';
 state.evidence[0].file='C:/Research One/input counts.tsv';
 state.analysis.summary='PRIVATE_GRAPH_SUMMARY';
 const output=researchHandoff(recommendation,state,'en');
 assert.match(output,/threshold=0.05/);assert.match(output,/END_REQUIRED/);assert.match(output,/C:\/Research One\/input counts.tsv/);
 assert.match(output,/> Parameters/);assert.doesNotMatch(output,/DO_NOT_SEND|PRIVATE_GRAPH_SUMMARY|evidenceIds|analysisStale/);
 assert.doesNotMatch(output,/[\p{Script=Han}]/u,'Application-owned task copy must follow the selected language');
});

test('visible handoff stays one short sentence even when topic and title are long',()=>{
 const h=fixture(),{researchHandoff,researchHandoffSummary}=h.get('views/knowledge-research-context.ts');
 const state=saved();state.topic='LONG_TOPIC_'.repeat(200);
 const suggestion={...recommendation,title:'FULL_RESEARCH_TITLE_\n'.repeat(40)};
 const summary=researchHandoffSummary(suggestion,state,'en'),context=researchHandoff(suggestion,state,'en');
 assert.ok(summary.length<180);assert.ok(!summary.includes('\n'));assert.doesNotMatch(summary,/LONG_TOPIC/);
 assert.ok(context.includes(state.topic));assert.ok(context.includes(suggestion.title));
});

test('duplicate clicks submit once and only explicit acceptance marks a handoff delivered',async()=>{
 let resolve;const prompts=[];
 const x=await hub({state:saved,overrides:{dshRpc:async(method,payload)=>{
  if(method==='workspace.list')return {items:projects};
  if(method==='research.handoff'){prompts.push(payload);return new Promise(yes=>{resolve=yes;});}return {};
 }}});
 try{
  sessionStorage.setItem('dsh-chat-unsent-draft','Keep this unsent draft');
  await click(x.view,'课题研究');await act(async()=>{x.view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});
  const submit=button(x.view,'交给主对话').props.onClick;
  await act(async()=>{submit();submit();await settle();});
  assert.equal(prompts.length,1);assert.equal(button(x.view,'正在交接…').props.disabled,true);
  assert.equal(x.calls.filter(c=>c.tab).length,0);assert.equal(button(x.view,'已交给主对话'),undefined);
  await act(async()=>{resolve({});await settle();});
  assert.equal(x.calls.filter(c=>c.tab).length,0);assert.match(text(x.view.toJSON()),/未能交给主对话/);
  await click(x.view,'交给主对话');assert.equal(prompts.length,2);assert.equal(prompts[1].requestId,prompts[0].requestId);
  await act(async()=>{resolve({accepted:true});await settle();});
  assert.equal(x.calls.at(-1).tab,'chat');assert.equal(button(x.view,'已交给主对话').props.disabled,true);
  assert.equal(sessionStorage.getItem('dsh-chat-unsent-draft'),'Keep this unsent draft');
 }finally{await x.close();}
});

test('an old running agent without the context plugin asks for restart instead of claiming delivery',async()=>{
 const x=await hub({state:saved,overrides:{dshRpc:async(method)=>{
  if(method==='workspace.list')return {items:projects};
  if(method==='research.handoff')throw Error('RESEARCH_HANDOFF_NOT_READY');
  return {};
 }}});
 try{
  await click(x.view,'课题研究');await act(async()=>{x.view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});await click(x.view,'交给主对话');
  assert.equal(x.calls.filter(c=>c.tab).length,0);
  assert.match(text(x.view.toJSON()),/请重启智能体后再交给主对话/);
  assert.equal(button(x.view,'已交给主对话'),undefined);
 }finally{await x.close();}
});

test('English hub and planning empty state translate every app-owned label',async()=>{
 const x=await hub();
 try{
  await click(x.view,'课题研究');await act(async()=>{x.view.root.findByProps({id:'research-tab-suggestions'}).props.onClick();await settle();});await act(async()=>{x.view.update(x.h.wrap(x.View,'en'));await settle();});
  assertEnglish(x.view);
 }finally{await x.close();}
});

test('research failures and graph controls have English translations',()=>{
 const h=fixture(),{researchFailure}=h.get('views/research-data.ts'),{translate}=h.get('i18n.ts');
 const messages=['CANCELLED','CONFLICT','CORRUPT','ACCOUNT_CHANGED','PROJECT','MODEL','STORAGE','BUSY','LIMIT','INVALID'].map(code=>researchFailure('RESEARCH_'+code));
 messages.push(researchFailure('UNAUTHORIZED'),researchFailure('FORBIDDEN'),researchFailure('unknown'),
  '图谱缩放','放大','缩小','显示完整图谱','按回车选择节点，按退出键取消选择。','按回车选择连线，按退出键取消选择。');
 for(const message of messages)assert.doesNotMatch(translate('en',message),/[\p{Script=Han}]/u,message);
});
