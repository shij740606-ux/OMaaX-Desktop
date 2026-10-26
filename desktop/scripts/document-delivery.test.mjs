import {test} from 'node:test';
import assert from 'node:assert/strict';
import {act,create,settle,text,fixture,assertEnglish} from './locale-test-utils.mjs';

const button=(view,label)=>view.root.findAllByType('button').find(node=>text(node)===label);
const click=async(view,label)=>{const node=button(view,label);assert.ok(node,label);await act(async()=>{await node.props.onClick();await settle();});};
const files=[
  {name:'report-v1.docx',rel:'documents/report-v1.docx',kind:'report'},
  {name:'slides-v1.pptx',rel:'documents/slides-v1.pptx',kind:'report'},
  {name:'report-v1.pdf',rel:'documents/report-v1.pdf',kind:'figure'},
  {name:'source.json',rel:'documents/source.json',kind:'meta'},
].map(f=>({...f,size:2048,modified:1700000000}));

test('current project documents open directly and continue editing asks for the requested change without rewriting',async()=>{
 const calls=[],h=fixture({state:{dsh:{state:'running',log:[]},currentSession:'s1',setCurrentSession:sessionId=>calls.push({sessionId}),setTab:tab=>calls.push({tab})},api:{
  dshRpc:async(method,payload)=>{calls.push({method,payload});return method==='workspace.list'?{items:[{workspaceId:'w1',title:'Project one',path:'C:/Workspace One',sessionIds:['s1']}]}:{};},
  workspaceFiles:async path=>path==='C:/Workspace One'?files:[],openPath:async path=>calls.push({open:path}),
 }}),View=h.get('views/DataView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View,'en'));await settle();});
 await click(view,'Project documents');
 const section=view.root.findByProps({'aria-label':'Project documents'});
 assert.equal(section.findAllByType('article').length,3);assert.ok(!text(section).includes('source.json'));assertEnglish(view);
 await click(view,'report-v1.docx');assert.equal(calls.at(-1).open,'C:/Workspace One\\documents\\report-v1.docx');
 await act(async()=>{await section.findAllByType('button').find(b=>text(b)===h.get('i18n.ts').translate('en','打开所在文件夹')).props.onClick();});
 assert.equal(calls.at(-1).open,'C:/Workspace One\\documents');
 await act(async()=>{await section.findAllByType('button').find(b=>text(b)==='Continue editing').props.onClick();await settle();});
 const request=calls.find(c=>c.method==='session.prompt');assert.equal(request.payload.sessionId,'s1');assert.match(request.payload.content[0].text,/C:\/Workspace One\\documents\\report-v1\.docx/);
 assert.match(request.payload.content[0].text,/First ask what I want changed and wait/);assert.match(request.payload.content[0].text,/preserving the previous version/);assert.equal(calls.at(-1).tab,'chat');
 await act(async()=>view.update(h.wrap(View,'zh')));assert.equal(view.root.findByProps({'aria-label':'项目文档'}).findAllByType('article').length,3);
 await act(async()=>view.unmount());
});

test('opening a missing document is recoverable and revision is unavailable while the agent is stopped',async()=>{
 const h=fixture({api:{workspaceFiles:async()=>files,openPath:async()=>{throw Error('missing');}}}),View=h.get('views/DataView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View,'en'));await settle();});
 await click(view,'Project documents');
 const section=view.root.findByProps({'aria-label':'Project documents'});
 assert.ok(section.findAllByType('button').filter(b=>text(b)==='Continue editing').every(b=>b.props.disabled));
 await click(view,'report-v1.pdf');assert.match(text(view.toJSON()),/Could not open the file/);
 await act(async()=>view.unmount());
});

test('editing another selected project starts a conversation in that project instead of the previous one',async()=>{
 const calls=[],projects=[{workspaceId:'w1',title:'Project one',path:'C:/Project',sessionIds:['s1']},{workspaceId:'w2',title:'Project two',path:'C:/Second',sessionIds:['s2']}];
 const h=fixture({state:{dsh:{state:'running',log:[]},currentSession:'s1',setCurrentSession:sessionId=>calls.push({current:sessionId}),setTab:tab=>calls.push({tab})},api:{
  dshRpc:async(method,payload)=>{calls.push({method,payload});return method==='workspace.list'?{items:projects}:{};},
  dshNewSession:async workspaceId=>{calls.push({newProject:workspaceId});return 's3';},workspaceFiles:async()=>files,
 }}),View=h.get('views/DataView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View));await settle();});await click(view,'Project documents');
 await act(async()=>{view.root.findByType('select').props.onChange({target:{value:'C:/Second'}});await settle();});
 await act(async()=>{view.root.findByProps({'aria-label':'Project documents'}).findAllByType('button').find(b=>text(b)==='Continue editing').props.onClick();await settle();});
 assert.equal(calls.find(c=>c.newProject)?.newProject,'w2');const request=calls.find(c=>c.method==='session.prompt');assert.equal(request.payload.sessionId,'s3');
 assert.match(request.payload.content[0].text,/C:\/Second\\documents\\report-v1\.docx/);assert.equal(calls.find(c=>c.current)?.current,'s3');assert.equal(calls.at(-1).tab,'chat');
 await act(async()=>view.unmount());
});

test('new project conversation remains selected when sending its revision request fails',async()=>{
 const calls=[],projects=[{workspaceId:'w1',title:'Project one',path:'C:/Project',sessionIds:['s1']},{workspaceId:'w2',title:'Project two',path:'C:/Second',sessionIds:['s2']}];
 const h=fixture({state:{dsh:{state:'running',log:[]},currentSession:'s1',setCurrentSession:sessionId=>calls.push({current:sessionId}),setTab:tab=>calls.push({tab})},api:{
  dshRpc:async(method,payload)=>{calls.push({method,payload});if(method==='session.prompt')throw Error('connection interrupted');return {items:projects};},
  dshNewSession:async workspaceId=>{calls.push({newProject:workspaceId});return 's3';},workspaceFiles:async()=>files,
 }}),View=h.get('views/DataView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View));await settle();});await click(view,'Project documents');
 await act(async()=>{view.root.findByType('select').props.onChange({target:{value:'C:/Second'}});await settle();});
 await act(async()=>{view.root.findByProps({'aria-label':'Project documents'}).findAllByType('button').find(b=>text(b)==='Continue editing').props.onClick();await settle();});
 assert.equal(calls.find(c=>c.newProject)?.newProject,'w2');
 assert.equal(calls.find(c=>c.current)?.current,'s3');
 assert.ok(calls.findIndex(c=>c.current==='s3')<calls.findIndex(c=>c.method==='session.prompt'),'State must follow the newly opened native conversation before sending');
 assert.equal(calls.find(c=>c.method==='session.prompt').payload.sessionId,'s3');
 assert.equal(calls.find(c=>c.tab),undefined);
 assert.match(text(view.toJSON()),/Could not continue editing the document/);
 assert.ok(view.root.findByProps({'aria-label':'Project documents'}).findAllByType('button').filter(b=>text(b)==='Continue editing').every(b=>!b.props.disabled));
 await act(async()=>view.unmount());
});

test('a late scan from the previous project cannot replace the selected project documents',async()=>{
 let resolveFirst;const h=fixture({state:{dsh:{state:'running',log:[]}},api:{
  dshRpc:async()=>({items:[{workspaceId:'w1',title:'Project one',path:'C:/Project',sessionIds:[]},{workspaceId:'w2',title:'Project two',path:'C:/Second',sessionIds:[]}]}),
  workspaceFiles:async path=>path==='C:/Project'?new Promise(resolve=>{resolveFirst=resolve;}):[{...files[0],name:'second.docx',rel:'documents/second.docx'}],
 }}),View=h.get('views/DataView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View));await settle();});
 await click(view,'Project documents');
 await act(async()=>{view.root.findByType('select').props.onChange({target:{value:'C:/Second'}});await settle();});
 assert.match(text(view.root.findByProps({'aria-label':'Project documents'})),/second\.docx/);
 await act(async()=>{resolveFirst(files);await settle();});
 const content=text(view.root.findByProps({'aria-label':'Project documents'}));assert.match(content,/second\.docx/);assert.doesNotMatch(content,/report-v1/);
 await act(async()=>view.unmount());
});

test('project documents follow a different conversation project while preserving the selected documents tab',async()=>{
 const projects=[{workspaceId:'w1',title:'Project one',path:'C:/Project',sessionIds:['s1']},{workspaceId:'w2',title:'Project two',path:'C:/Second',sessionIds:['s2']}];
 const h=fixture({state:{dsh:{state:'running',log:[]},currentSession:'s1'},api:{
  dshRpc:async()=>({items:projects}),workspaceFiles:async path=>[{...files[0],name:path==='C:/Second'?'second.docx':'first.docx',rel:path==='C:/Second'?'documents/second.docx':'documents/first.docx'}],
 }}),View=h.get('views/DataView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View));await settle();});await click(view,'Project documents');
 assert.match(text(view.root.findByProps({'aria-label':'Project documents'})),/first\.docx/);
 h.app.currentSession='s2';await act(async()=>{view.update(h.wrap(View));await settle();});
 assert.equal(view.root.findByType('select').props.value,'C:/Second');
 const content=text(view.root.findByProps({'aria-label':'Project documents'}));assert.match(content,/second\.docx/);assert.doesNotMatch(content,/first\.docx/);
 await act(async()=>view.unmount());
});

test('the documents tab has an English empty state and excludes unrelated project files',async()=>{
 const h=fixture({api:{workspaceFiles:async()=>[files[3]]}}),View=h.get('views/DataView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View));await settle();});await click(view,'Project documents');
 assert.match(text(view.toJSON()),/This project has no Word, PowerPoint or PDF documents yet/);assertEnglish(view);
 await act(async()=>view.update(h.wrap(View,'zh')));assert.match(text(view.toJSON()),/当前项目还没有 Word、PPT 或 PDF 文档/);
 await act(async()=>view.unmount());
});

test('chat retains server analysis files and project documents stay inside analysis results',async()=>{
 for(const servers of [[],['server-175']]){
  const tabs=[],h=fixture({state:{dsh:{state:'running',log:[]},currentSession:'s1',setTab:tab=>tabs.push(tab)},api:{
   dshSetReasoning:async()=>{},dshBounds:()=>{},dshRpc:async method=>method==='server.files'?servers:method==='session.reasoning'?{levels:['high'],selected:'high'}:{},
  }}),View=h.get('views/ChatView.tsx').default;let view;
  await act(async()=>{view=create(h.wrap(View,'en'));await settle();});
  await click(view,'Current analysis files');assert.equal(tabs.at(-1),servers.length?'hosts':'data');
  if(servers.length)assert.equal(JSON.parse(sessionStorage.getItem('omaax.files.open')).serverId,'server-175');
  assert.equal(button(view,'Project documents'),undefined);assertEnglish(view);
  await act(async()=>view.unmount());
 }
});
