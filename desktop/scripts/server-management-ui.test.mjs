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
const host={id:'test-server',name:'测试服务器',host:'127.0.0.1',port:22,username:'test',auth:'password',hostKey:'trusted',selected:true};
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
   if(name==='react')return React;if(name==='react/jsx-runtime')return realRequire(name);
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
function newConnectionApi({failAuth=false,failSelect=false}={}){
 let rows=[],calls=[];
 return {calls,api:{
  sshHosts:async()=>rows,
  sshHostSave:async draft=>{calls.push(['save',draft]);rows=[{...host,...draft,id:'fresh',hostKey:null,selected:false}];return rows;},
  sshHostTest:async(id,probe)=>{calls.push(['test',probe]);if(!probe&&failAuth)throw Error('密码验证失败');return {hostKey:'new-key',fingerprint:'SHA256:new',connected:true};},
  sshHostTrust:async(id,key,fingerprint)=>{calls.push(['trust']);rows=rows.map(h=>({...h,hostKey:key,fingerprint}));return rows;},
  sshHostSelect:async(id,selected)=>{calls.push(['select',selected]);if(failSelect)throw Error('保存选择失败');rows=rows.map(h=>({...h,selected}));return rows;},
  personalCompute:async(id,action)=>{calls.push([action]);return action==='initialize'?{workspace:'/home/test/omaax/workspace',setup:{state:'idle'}}:{state:'complete',detail:'基础环境就绪'};}
 }};
}
async function saveNewConnection(view){
 await act(async()=>button(view,'添加服务器').props.onClick());
 const labels=view.root.findByType('form').findAllByType('label');
 for(const [name,value] of [['自定义名字','新服务器'],['IP 或主机名','example.invalid'],['用户名','tester'],['密码','test-only-secret']]){
  const label=labels.find(l=>text(l)===name);await act(async()=>label.findByType('input').props.onChange({target:{value}}));
 }
 await act(async()=>{view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});
}

test('new connection saves credentials before probing and keeps fingerprint confirmation in its card',async()=>{
 const fixture=newConnectionApi(),h=harness(fixture.api),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});await saveNewConnection(view);
 assert.equal(fixture.calls[0][0],'save');assert.deepEqual(fixture.calls.filter(c=>c[0]==='test'),[['test',true]]);
 const trust=button(view,'信任此主机并测试连接');assert.ok(trust);assert.match(text(trust.parent.parent.parent),/新服务器/);
 assert.equal(view.root.findByProps({'aria-label':'允许智能体使用 新服务器'}).props.disabled,false);
 assert.doesNotMatch(text(view.root),/查询 \/ 刷新资源|服务器当前用量/);
 await act(async()=>{trust.props.onClick();await settle();});
 assert.deepEqual(fixture.calls.filter(c=>c[0]==='test'),[['test',true],['test',false]]);
 assert.match(text(view.root),/认证：已通过/);assert.equal(fixture.calls.filter(c=>c[0]==='initialize').length,1);
 await act(async()=>view.unmount());
});

test('enable fresh server continues after trust; cancel never authenticates or selects',async()=>{
 const fixture=newConnectionApi(),h=harness(fixture.api),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});await saveNewConnection(view);
 await act(async()=>button(view,'取消').props.onClick());
 assert.equal(fixture.calls.filter(c=>c[0]==='trust'||c[0]==='select'||c[0]==='test'&&!c[1]).length,0);
 await act(async()=>{view.root.findByProps({'aria-label':'允许智能体使用 新服务器'}).props.onChange({target:{checked:true}});await settle();});
 await act(async()=>{button(view,'信任此主机并测试连接').props.onClick();await settle();});
 assert.equal(view.root.findByProps({'aria-label':'允许智能体使用 新服务器'}).props.checked,true);
 assert.deepEqual(fixture.calls.filter(c=>c[0]==='select'),[['select',true]]);await act(async()=>view.unmount());
});

test('wrong password does not enable agent or prepare environment',async()=>{
 const fixture=newConnectionApi({failAuth:true}),h=harness(fixture.api),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});await saveNewConnection(view);
 await act(async()=>{button(view,'信任此主机并测试连接').props.onClick();await settle();});
 assert.match(text(view.root),/连接：已验证可达 · 认证：未通过/);assert.match(text(view.root),/SSH 认证未通过/);
 assert.equal(fixture.calls.filter(c=>c[0]==='initialize'||c[0]==='select').length,0);await act(async()=>view.unmount());
});

test('a selection persistence failure does not misreport successful SSH authentication',async()=>{
 const fixture=newConnectionApi({failSelect:true}),h=harness(fixture.api),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});await saveNewConnection(view);
 await act(async()=>button(view,'取消').props.onClick());
 await act(async()=>{view.root.findByProps({'aria-label':'允许智能体使用 新服务器'}).props.onChange({target:{checked:true}});await settle();});
 await act(async()=>{button(view,'信任此主机并测试连接').props.onClick();await settle();});
 assert.match(text(view.root),/认证：已通过/);assert.match(text(view.root),/保存选择失败/);await act(async()=>view.unmount());
});

test('SFTP browser navigates, pages, filters and copies without requesting an analysis environment',async()=>{
 const calls=[],copied=[];Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{writeText:async path=>copied.push(path)}}});
 const entry=(name,directory=false)=>({name,path:'/home/test/'+name,directory,link:false,size:12,modified:1,permissions:'644'});
 const h=harness({sshHosts:async()=>[host],personalCompute:async()=>{throw Error('analysis environment must not be queried');},sshHostFiles:async(id,body)=>{
  calls.push(body);return {path:body.path==='/missing'?'/missing':'/home/test',home:'/home/test',parent:'/home',files:body.offset?[entry('tail.txt')]:[entry('folder',true),entry('中文.txt')],nextOffset:body.offset?null:2};
 }}),View=h.load('views/PersonalComputeView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{section:'files'}));await settle();});
 assert.equal(calls.length,1);assert.match(text(view.root),/中文.txt/);
 await act(async()=>{button(view,'加载更多').props.onClick();await settle();});assert.equal(calls.at(-1).offset,2);assert.match(text(view.root),/tail.txt/);
 await act(async()=>view.root.findByProps({'aria-label':'筛选已加载文件'}).props.onChange({target:{value:'中文'}}));assert.doesNotMatch(text(view.root),/tail.txt/);
 await act(async()=>{view.root.findByProps({'aria-label':'复制路径 中文.txt'}).props.onClick();await settle();});assert.deepEqual(copied,['/home/test/中文.txt']);
 await act(async()=>{view.root.findAllByType('input').find(n=>n.props.type==='checkbox').props.onChange({target:{checked:true}});await settle();});assert.equal(calls.at(-1).showHidden,true);assert.equal(calls.at(-1).offset,0);
 await act(async()=>view.root.findByProps({'aria-label':'服务器目录路径'}).props.onChange({target:{value:'/missing'}}));
 await act(async()=>{view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});assert.equal(calls.at(-1).path,'/missing');
 await act(async()=>view.unmount());
});

test('SFTP errors are visible and late directory responses do not replace a refreshed view',async()=>{
 let finish,count=0;const h=harness({sshHostFiles:async()=>{count++;if(count===1)return new Promise(resolve=>finish=resolve);if(count===2)throw Error('没有读取权限');return {path:'/new',home:'/home/test',parent:'/',files:[],nextOffset:null};}}),View=h.load('components/ServerFileBrowser.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{server:host.id}));await settle();});
 await act(async()=>{view.update(React.createElement(View,{server:host.id,refreshToken:1}));await settle();});assert.match(text(view.root),/当前账号没有完成此操作的权限/);
 await act(async()=>{view.update(React.createElement(View,{server:host.id,refreshToken:2}));await settle();});
 await act(async()=>{finish({path:'/old',home:'/home/test',parent:'/',files:[],nextOffset:null});await settle();});
 assert.equal(view.root.findByProps({'aria-label':'服务器目录路径'}).props.value,'/new');assert.doesNotMatch(text(view.root),/没有完成此操作的权限/);await act(async()=>view.unmount());
});

const card=(view,name)=>view.root.findByProps({'aria-label':'服务器 '+name});
const withinButton=(node,label)=>node.findAllByType('button').find(b=>text(b)===label);
test('one server test never locks other cards; duplicate clicks are ignored and errors stay on their card',async()=>{
 let finish;const calls=[],a={...host,id:'a',name:'A'},b={...host,id:'b',name:'B'};
 const h=harness({sshHosts:async()=>[a,b],sshHostTest:async(id,probe)=>{
  calls.push([id,probe]);if(id==='a'&&probe)return new Promise(resolve=>finish=resolve);
  if(id==='b')throw Error('Timed out while waiting for handshake');return {hostKey:'trusted',fingerprint:'SHA256:trusted'};
 },personalCompute:async(id,action)=>action==='initialize'?{workspace:'/test/'+id,setup:{state:'complete',detail:'环境已就绪'}}:{state:'complete',detail:'环境已就绪'}}),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});
 const click=withinButton(card(view,'A'),'测试连接').props.onClick;
 await act(async()=>{click();click();await settle();});assert.equal(calls.length,1);
 assert.equal(withinButton(card(view,'B'),'测试连接').props.disabled,false);
 assert.equal(card(view,'B').findByProps({'aria-label':'编辑服务器'}).props.disabled,false);
 assert.equal(view.root.findByProps({'aria-label':'允许智能体使用 B'}).props.disabled,false);
 assert.equal(button(view,'添加服务器').props.disabled,false);
 await act(async()=>{withinButton(card(view,'B'),'测试连接').props.onClick();await settle();});
 assert.match(text(card(view,'B')),/SSH 握手超时/);assert.doesNotMatch(text(card(view,'A')),/握手超时/);
 assert.equal(withinButton(card(view,'A'),'正在处理…').props.disabled,true);
 await act(async()=>{finish({hostKey:'trusted',fingerprint:'SHA256:trusted'});await settle();});
 assert.match(text(card(view,'A')),/认证：已通过/);assert.match(text(card(view,'B')),/SSH 握手超时/);
 assert.equal(withinButton(card(view,'A'),'测试连接').props.disabled,false);await act(async()=>view.unmount());
});

test('two first-connection fingerprint panels coexist and cancelling one does not cancel the other',async()=>{
 let rows=[{...host,id:'a',name:'A',hostKey:null,selected:false},{...host,id:'b',name:'B',hostKey:null,selected:false}],auth=[];
 const h=harness({sshHosts:async()=>rows,sshHostTest:async(id,probe)=>{if(!probe)auth.push(id);return {hostKey:'key-'+id,fingerprint:'SHA256:'+id};},sshHostTrust:async(id,key)=>{rows=rows.map(r=>r.id===id?{...r,hostKey:key}:r);return rows;},personalCompute:async()=>({setup:{state:'complete',detail:'就绪'},state:'complete',detail:'就绪',workspace:'/test'})}),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});
 await act(async()=>{withinButton(card(view,'A'),'测试连接').props.onClick();withinButton(card(view,'B'),'测试连接').props.onClick();await settle();});
 assert.match(text(card(view,'A')),/SHA256:a/);assert.match(text(card(view,'B')),/SHA256:b/);assert.equal(auth.length,0);
 await act(async()=>withinButton(card(view,'A'),'取消').props.onClick());
 assert.doesNotMatch(text(card(view,'A')),/SHA256:a/);assert.match(text(card(view,'B')),/SHA256:b/);
 await act(async()=>{withinButton(card(view,'B'),'信任此主机并测试连接').props.onClick();await settle();});
 assert.deepEqual(auth,['b']);assert.match(text(card(view,'B')),/认证：已通过/);assert.doesNotMatch(text(card(view,'A')),/认证：已通过/);await act(async()=>view.unmount());
});

test('out-of-order selection responses cannot overwrite another server selection',async()=>{
 const a={...host,id:'a',name:'A',selected:false},b={...host,id:'b',name:'B',selected:false},resolvers={};
 const h=harness({sshHosts:async()=>[a,b],sshHostTest:async()=>({hostKey:'trusted',fingerprint:'SHA256:t'}),sshHostSelect:async(id)=>new Promise(resolve=>resolvers[id]=resolve),personalCompute:async()=>({setup:{state:'complete'},state:'complete',workspace:'/test'})}),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});
 await act(async()=>{view.root.findByProps({'aria-label':'允许智能体使用 A'}).props.onChange({target:{checked:true}});view.root.findByProps({'aria-label':'允许智能体使用 B'}).props.onChange({target:{checked:true}});await settle();});
 await act(async()=>{resolvers.b([{...a,selected:true},{...b,selected:true}]);await settle();});
 await act(async()=>{resolvers.a([{...a,selected:true},b]);await settle();});
 assert.equal(view.root.findByProps({'aria-label':'允许智能体使用 A'}).props.checked,true);
 assert.equal(view.root.findByProps({'aria-label':'允许智能体使用 B'}).props.checked,true);await act(async()=>view.unmount());
});

test('task management no longer exposes manual command submission',async()=>{
 const calls=[];const h=harness({sshHosts:async()=>[host],personalCompute:async(id,action)=>{calls.push(action);return action==='status'?{root:'/test',sources:{},images:[],protection:{},error:''}:[];}}),View=h.load('views/PersonalComputeView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{section:'tasks'}));await settle();});
 assert.match(text(view.root),/安排分析任务/);assert.doesNotMatch(text(view.root),/高级设置|手动提交|候选服务器|命令参数|自动选择服务器并提交/);
 assert.ok(!calls.includes('schedule'));await act(async()=>view.unmount());
});

test('server errors have plain Chinese explanations instead of logs, commands or stack traces',()=>{
 const h=harness({}),{serverError}=h.load('serverErrors.ts');
 const cases=[
  ['Timed out while waiting for handshake','connection',/SSH 握手超时/],
  ['All configured authentication methods failed','connection',/SSH 认证未通过/],
  ['Host denied (verification failed)','connection',/指纹/],
  ['getaddrinfo ENOTFOUND example.invalid','connection',/无法解析/],
  ['connect ECONNREFUSED 127.0.0.1:22','connection',/拒绝连接/],
  ['No route to host','connection',/无法到达/],
  ['Trying to pull docker.io/library/python:3.12-slim... level=warning msg="registry-1.docker.io connection refused"','environment',/无法连接镜像仓库/],
  ['registry.example: x509 certificate has expired','environment',/证书验证失败/],
  ['podman pull: manifest unknown registry.example','environment',/镜像不可用/],
  ['No space left on device','environment',/磁盘空间不足/],
  ['Disk quota exceeded','files',/存储配额不足/],
  ['Permission denied','files',/没有完成此操作的权限/],
  ['ENOENT: no such file or directory','files',/不存在/],
  ['Transfer checksum mismatch','transfer',/校验未通过/],
  ['ECONNRESET: socket hang up','transfer',/连接已中断/],
  ['command timed out after 180 seconds','environment',/环境准备或检查超时/],
  ['database is locked','operation',/任务记录/],
  ['Traceback (most recent call last):\nTypeError: unexpected raw internal code','files',/文件操作未完成/]
 ];
 for(const [raw,scope,expected] of cases){const value=serverError(raw,scope);assert.match(value,expected);assert.doesNotMatch(value,/Traceback|level=|ECONN|ENOENT|docker\.io|TypeError/);}
 assert.equal(serverError('目标位置已有文件，不能覆盖'),'目标位置已有文件，不能覆盖');
});

test('raw environment failure is retained only in collapsed diagnostics and never replaces authentication',async()=>{
 const raw='Trying to pull docker.io/library/python:3.12-slim... level=warning msg="registry-1.docker.io connection refused"';
 const h=harness({sshHosts:async()=>[host],sshHostTest:async()=>({hostKey:'trusted',fingerprint:'SHA256:t'}),personalCompute:async(id,action)=>action==='initialize'?{workspace:'/test',setup:{state:'failed',detail:raw}}:{state:'failed',detail:raw}}),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});
 await act(async()=>{button(view,'测试连接').props.onClick();await settle();});
 const connections=h.load('serverConnections.ts'),setup=connections.connectionState(host.id).setup;
 assert.match(setup.detail,/服务器无法连接镜像仓库/);assert.equal(setup.errorDetail,raw);assert.match(text(view.root),/认证：已通过/);
 const diagnostic=view.root.findAllByType('details').find(d=>text(d).includes(raw));assert.ok(diagnostic);assert.ok(!diagnostic.props.open);
 const visible=node=>typeof node==='string'?node:node.type==='details'&&!node.props.open?text(node.findByType('summary')):(node.children||[]).map(visible).join('');
 assert.doesNotMatch(visible(view.root),/Trying to pull|level=|registry-1/);await act(async()=>view.unmount());
});
test('SSH success remains visible when automatic environment setup fails; remount does not reinstall',async()=>{
 let starts=0,authenticated=0;
 const h=harness({sshHosts:async()=>[host],sshHostTest:async(id,probe)=>{if(!probe)authenticated++;return {hostKey:'trusted',fingerprint:'SHA256:test',connected:true};},personalCompute:async(id,action)=>{
  if(action==='initialize')return {workspace:'/home/test/omaax/workspace',setup:{state:'idle',detail:'未准备'}};
  if(action==='setup_start'){starts++;throw Error('registry unreachable');}throw Error(action);
 }}),View=h.load('views/HostsView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});
 await act(async()=>{view.root.findAllByType('button').find(b=>text(b)==='测试连接').props.onClick();await settle();});
 assert.equal(authenticated,1);assert.equal(starts,1);assert.match(text(view.root),/认证：已通过/);assert.match(text(view.root),/registry unreachable/);assert.match(text(view.root),/专属工作目录/);
 await act(async()=>{view.unmount();await settle();view=create(React.createElement(View));await settle();});
 assert.equal(starts,1);assert.match(text(view.root),/认证：已通过/);await act(async()=>view.unmount());
});

test('cross-server data contains transfers only; file management remains in protection',async()=>{
 const calls=[];const h=harness({sshHosts:async()=>[host],personalCompute:async(id,action,body)=>{
  calls.push(action);if(action==='status')return {root:'/test/control',sources:{shared:'/test/shared'},images:[],protection:null,error:''};
  if(action==='workspace_files'){if(body.operation==='info')return {workspaceRoot:'/test/workspace',path:'/test/workspace'};if(body.operation==='contexts')return {items:[]};return {files:[]};}return [];
 }}),View=h.load('views/PersonalComputeView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{section:'data'}));await settle();});
 assert.match(text(view.root),/数据传输记录/);assert.match(text(view.root),/复制数据/);assert.doesNotMatch(text(view.root),/项目分析文件|保护原始数据/);
 assert.ok(!calls.some(action=>['server_delete_requests','server_write_requests','workspace_files'].includes(action)));
 await act(async()=>{view.update(React.createElement(View,{section:'protection'}));await settle();});
 assert.match(text(view.root),/项目分析文件/);assert.match(text(view.root),/保护原始数据/);assert.ok(calls.includes('server_delete_requests'));await act(async()=>view.unmount());
});

test('environment management only reads on entry and configures exactly the selected steps',async()=>{
 const calls=[];const h=harness({personalCompute:async(id,action,body)=>{
  calls.push({action,body});if(action==='server_environments')return {images:[],error:'',setup:{state:'complete',detail:'基础环境就绪'},checkedAt:Date.now()/1000,conda:{environments:[{name:'existing-r',path:'/home/test/conda/envs/existing-r'}],error:''}};
  if(action==='workflow_prepare_status')return {state:'idle',detail:'尚未配置'};
  if(action==='workflow_readiness')return {steps:Object.fromEntries(body.steps.map(id=>[id,{state:'ready',detail:'软件齐全'}]))};
  if(action==='workflow_prepare')return {id:'test-install',state:'running',detail:'仅安装所选步骤',request:{steps:body.steps}};throw Error(action);
 }}),View=h.load('components/ServerEnvironments.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{server:host.id,label:host.name}));await settle();});
 assert.equal(calls.filter(c=>['setup_start','workflow_prepare','workflow_readiness'].includes(c.action)).length,0);
 assert.equal(calls.find(c=>c.action==='server_environments').body.conda,true);assert.match(text(view.root),/existing-r/);
 const checkbox=view.root.findAllByType('input').find(n=>n.props.type==='checkbox'&&!n.props.disabled);await act(async()=>checkbox.props.onChange({target:{checked:true}}));
 await act(async()=>{await view.root.findAllByType('button').find(b=>text(b)==='检查所需软件').props.onClick();await settle();});
 const wanted=calls.find(c=>c.action==='workflow_readiness').body.steps;assert.equal(wanted.length,1);
 await act(async()=>{await view.root.findAllByType('button').find(b=>text(b)==='在对话中配置环境').props.onClick();await settle();});
 assert.equal(calls.filter(c=>c.action==='workflow_prepare').length,0);assert.ok(localStorage.getItem('omaax.workflow.view'));
 await act(async()=>view.unmount());
 await act(async()=>{view=create(React.createElement(View,{server:host.id,label:host.name}));await settle();});
 assert.equal(view.root.findAllByType('input').filter(n=>n.props.checked).length,1);assert.equal(calls.filter(c=>c.action==='workflow_prepare').length,0);await act(async()=>view.unmount());
});

test('file links open protection, and management exposes an environment section',async()=>{
 const h=harness({sshHosts:async()=>[host],personalCompute:async(id,action,body)=>{
  if(action==='status')return {root:'/test',sources:{},images:[],protection:null,error:''};
  if(action==='workspace_files')return body.operation==='contexts'?{items:[]}:body.operation==='info'?{workspaceRoot:'/test',path:'/test'}:{files:[]};return [];
 }}),View=h.load('views/ServerManagementView.tsx').default;
 sessionStorage.setItem('omaax.files.open',JSON.stringify({serverId:host.id,sessionId:'test-dialogue'}));
 let view;await act(async()=>{view=create(React.createElement(View));await settle();});
 assert.equal(view.root.findAllByType('button').find(b=>text(b)==='数据保护与审计').props['aria-pressed'],true);
 assert.ok(view.root.findAllByType('button').some(b=>text(b)==='环境管理'));await act(async()=>view.unmount());
});

test('switching servers ignores late environment inventories and does not start installers',async()=>{
 let finish;const calls=[];
 const h=harness({personalCompute:async(id,action)=>{
  calls.push(action);if(action==='workflow_prepare_status')return {state:'failed',detail:'已有安装失败记录，可重试',log:'test install log'};
  if(action==='server_environments'){if(id==='old')return new Promise(resolve=>{finish=resolve;});return {images:[],error:'测试：容器组件不可用',setup:{state:'idle',detail:'待准备'},checkedAt:Date.now()/1000,conda:{environments:[{name:'NEW_SERVER_ENV',path:'/new/env'}],error:''}};}throw Error(action);
 }}),View=h.load('components/ServerEnvironments.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{server:'old',label:'old'}));await settle();});
 await act(async()=>{view.update(React.createElement(View,{server:'new',label:'new'}));await settle();});
 await act(async()=>{finish({images:[],error:'OLD_SERVER_ERROR',setup:{state:'idle',detail:'old'},checkedAt:Date.now()/1000});await settle();});
 assert.match(text(view.root),/NEW_SERVER_ENV/);assert.match(text(view.root),/容器组件不可用/);assert.match(text(view.root),/已有安装失败记录/);assert.doesNotMatch(text(view.root),/OLD_SERVER_ERROR/);
 assert.ok(!calls.includes('workflow_prepare'));await act(async()=>view.unmount());
});

test('environment inventory remains visible while manual association is removed',async()=>{
 const calls=[],image='sha256:'+'a'.repeat(64);
 const h=harness({personalCompute:async(id,action,body)=>{
  calls.push({action,body});if(action==='server_environments')return {images:[{id:image,tags:[],size:100,installed:true,approved:true,bindings:[],activeJobs:0}],error:'',setup:{state:'complete',detail:'已就绪'},checkedAt:Date.now()/1000,conda:{environments:[],error:''}};
  if(action==='workflow_prepare_status')return {state:'idle',detail:'未配置'};if(action==='workflow_environment')return {ready:true};throw Error(action);
 }}),View=h.load('components/ServerEnvironments.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{server:'associate',label:'测试'}));await settle();});
 assert.equal(view.root.findAllByProps({'aria-label':'已有服务器分析环境'}).length,0);assert.ok(!calls.some(c=>c.action==='workflow_environment'||c.action==='workflow_readiness'));assert.match(text(view.root),/已安装/);await act(async()=>view.unmount());
});

test('completed installation clears stale missing-software labels without launching another check',async()=>{
 let state='idle';const calls=[];
 const h=harness({personalCompute:async(id,action,body)=>{
  calls.push(action);if(action==='server_environments')return {images:[],error:'',setup:{state:'complete',detail:'就绪'},checkedAt:Date.now()/1000,conda:{environments:[],error:''}};
  if(action==='workflow_prepare_status')return {id:'install-done',state,detail:state==='complete'?'本次已配置完成':'等待配置'};
  if(action==='workflow_readiness')return {steps:Object.fromEntries(body.steps.map(s=>[s,{state:'missing_environment',detail:'测试缺少软件'}]))};
  if(action==='workflow_prepare'){state='running';return {id:'install-done',state,detail:'配置中'};}throw Error(action);
 }}),View=h.load('components/ServerEnvironments.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{server:'completion',label:'测试'}));await settle();});
 const checkbox=view.root.findAllByType('input').find(n=>n.props.type==='checkbox'&&!n.props.disabled),name=checkbox.props['aria-label'];
 const row=()=>view.root.findAllByType('label').find(l=>l.findAllByType('input').some(n=>n.props['aria-label']===name));
 await act(async()=>checkbox.props.onChange({target:{checked:true}}));
 await act(async()=>{await view.root.findAllByType('button').find(b=>text(b)==='检查所需软件').props.onClick();await settle();});assert.match(text(row()),/缺少环境/);
 await act(async()=>{await view.root.findAllByType('button').find(b=>text(b)==='在对话中配置环境').props.onClick();await settle();});
 state='complete';await act(async()=>{for(const tick of h.timers)tick();await settle();});
 assert.doesNotMatch(text(row()),/缺少环境/);assert.match(text(row()),/尚未检查/);assert.equal(calls.filter(a=>a==='workflow_readiness').length,1);await act(async()=>view.unmount());
});
test('failed privileged installation can be dismissed and no longer blocks closing',async()=>{
 const h=harness({personalCompute:async()=>{throw Error('sudo denied');}}),{default:Install,SudoInstallProvider}=h.load('components/SudoInstall.tsx');
 let view;await act(async()=>{view=create(React.createElement(SudoInstallProvider,null,React.createElement(Install,{id:'test-server',label:'测试服务器'})));await settle();});
 await act(async()=>view.root.findAllByType('button').find(b=>text(b)==='授权安装').props.onClick());
 await act(async()=>view.root.findByType('input').props.onChange({target:{value:'temporary-test-password'}}));
 await act(async()=>{view.root.findByType('form').props.onSubmit({preventDefault(){}});await settle();});
 assert.match(text(view.root),/安装未完成/);const closeEvent=new Event('beforeunload',{cancelable:true});h.events.dispatchEvent(closeEvent);assert.equal(closeEvent.defaultPrevented,false);
 await act(async()=>view.root.findAllByType('button').find(b=>text(b)==='确认结果并关闭').props.onClick());
 assert.equal(view.root.findAll(n=>n.props.role==='dialog').length,0);await act(async()=>view.unmount());
});
test('late resource responses cannot replace a newly selected management section',async()=>{
 let finish;const h=harness({sshHosts:async()=>[host],sshHostMetrics:()=>new Promise(resolve=>{finish=resolve;}),personalCompute:async(id,action)=>action==='status'?{root:'/home/test/omaax',sources:{},images:[],protection:null,error:'尚未配置'}:[]}),View=h.load('views/PersonalComputeView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{section:'resources'}));await settle();});
 await act(async()=>{view.update(React.createElement(View,{section:'tasks'}));await settle();});
 await act(async()=>{finish({cpuModel:'STALE_RESOURCE_RESPONSE'});await settle();});
 assert.match(text(view.root),/安排分析任务/);assert.doesNotMatch(text(view.root),/STALE_RESOURCE_RESPONSE/);await act(async()=>view.unmount());
});
test('editing a connection invalidates its old pending setup and permits a fresh check',async()=>{
 let oldResponse,initializations=0,starts=0;
 const h=harness({personalCompute:async(id,action)=>{
  if(action==='initialize'){initializations++;if(initializations===1)return new Promise(resolve=>{oldResponse=resolve;});return {workspace:'/new-server/omaax/workspace',setup:{state:'idle',detail:'new server'}};}
  if(action==='setup_start'){starts++;return {state:'complete',detail:'new environment'};}throw Error(action);
 }}),connections=h.load('serverConnections.ts');
 const old=connections.prepareServer('edited');connections.resetConnection('edited');await connections.prepareServer('edited');
 oldResponse({workspace:'/old-server/omaax/workspace',setup:{state:'failed',detail:'old'}});await old;
 assert.equal(starts,1);assert.equal(connections.connectionState('edited').workspace,'/new-server/omaax/workspace');assert.equal(connections.connectionState('edited').setup.state,'complete');
});
test('cached resources from a previous endpoint are not shown after connection editing',async()=>{
 let calls=0,finish;
 const metric={cpuModel:'OLD_ENDPOINT_CPU',cpuPercent:10,logicalCores:4,physicalCores:2,memoryTotal:100,memoryUsed:20,swapTotal:0,swapUsed:0,load:[0,0,0],disks:[],hostname:'test',system:'Linux',architecture:'x64',uptimeSeconds:1,sampledAt:1};
 const h=harness({sshHosts:async()=>[host],sshHostMetrics:async()=>{if(++calls===1)return metric;return new Promise(resolve=>{finish=resolve;});}}),View=h.load('views/PersonalComputeView.tsx').default;
 let view;await act(async()=>{view=create(React.createElement(View,{section:'resources'}));await settle();});assert.match(text(view.root),/OLD_ENDPOINT_CPU/);
 await act(async()=>view.unmount());h.load('serverConnections.ts').resetConnection(host.id);
 await act(async()=>{view=create(React.createElement(View,{section:'resources'}));await settle();});assert.doesNotMatch(text(view.root),/OLD_ENDPOINT_CPU/);
 await act(async()=>{finish({...metric,cpuModel:'NEW_ENDPOINT_CPU'});await settle();});assert.match(text(view.root),/NEW_ENDPOINT_CPU/);await act(async()=>view.unmount());
});
