import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

import {root,React,act,create,settle,text,han,fixture,assertEnglish} from './locale-test-utils.mjs';

test('translation templates preserve values, punctuation and dollar signs without translating user data',()=>{
 const {translate}=fixture().get('i18n.ts');
 assert.equal(translate('en','找到 {count} 条帮助',{count:14}),'14 help topics found');
 assert.equal(translate('en','找到 14 条帮助'),'14 help topics found');
 assert.equal(translate('zh','目标：{name}',{name:'$& {count} 数据'}),'目标：$& {count} 数据');
 assert.equal(translate('en','目标：{name}',{name:'原始数据/$&'}),'Target: 原始数据/$&');
 assert.equal(translate('en','研究项目（我的名字）'),'研究项目（我的名字）');
 for(const value of ['__proto__','constructor','toString'])assert.equal(translate('en',value),value);
 assert.doesNotMatch(translate('en','相互作用分析未完成：蛋白缺少显式氢原子。请先完成结构加氢和质子化，再分析相互作用。原文件未修改。'),han);
 assert.doesNotMatch(translate('en','Error: 登录服务暂不可用（503），请稍后重试或联系管理员'),han);
});

test('settings, all menu details and help become English, switch back live, and preserve unsaved inputs',async()=>{
 const h=fixture(),View=h.get('views/SettingsView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View));await settle();});assertEnglish(view);
 assert.match(text(view.toJSON()),/Account & usage/);assert.match(text(view.toJSON()),/Help, version and licenses/);
 await act(async()=>view.root.findByProps({id:'settings-tab-tools'}).props.onClick());
 const endpoint=view.root.findAllByType('input').find(n=>n.props.placeholder==='https://…');
 await act(async()=>endpoint.props.onChange({target:{value:'https://example.invalid/模型路径'}}));
 await act(async()=>view.update(h.wrap(View,'zh')));assert.match(text(view.toJSON()),/账号和用量/);
 assert.equal(view.root.findAllByType('input').find(n=>n.props.placeholder==='https://…').props.value,'https://example.invalid/模型路径');
 await act(async()=>view.update(h.wrap(View,'en')));assertEnglish(view);
 await act(async()=>view.unmount());
});

test('English help search finds translated text and returns localized counts',async()=>{
 const h=fixture(),View=h.get('views/AboutHelp.tsx').default;let view;await act(async()=>{view=create(h.wrap(View));});
 await act(async()=>view.root.findByType('input').props.onChange({target:{value:'server'}}));
 assert.ok(view.root.findAllByType('details').length>0);assertEnglish(view);assert.match(text(view.toJSON()),/help topics found/);
 await act(async()=>view.unmount());
});

test('sidebar labels translate while custom Chinese project and conversation names stay unchanged',async()=>{
 const h=fixture({state:{dsh:{state:'running',log:[]}},api:{dshRpc:async method=>method==='workspace.list'?{items:[{workspaceId:'project',path:'C:/实验',title:'自定义项目',sessionIds:['session'],updatedAt:'2026-01-01'}],archivedSessionIds:[]}:{items:[{sessionId:'session',updatedAt:0,blank:false,running:false,projections:{values:{title:'服务器管理'}}}]}}});
 const View=h.get('components/Sidebar.tsx').default;let view;await act(async()=>{view=create(h.wrap(View));await settle();});
 assertEnglish(view,['自定义项目','服务器管理','实验']);
 assert.match(text(view.toJSON()),/Knowledge Q&A/);assert.match(text(view.toJSON()),/Scientific drawing/);
 assert.match(text(view.toJSON()),/服务器管理/);await act(async()=>view.unmount());
});

test('global installation dialog and status labels use current language without changing target identifiers',async()=>{
 const h=fixture(),{default:Button,SudoInstallProvider}=h.get('components/SudoInstall.tsx');let view;
 const tree=lang=>React.createElement(h.get('i18n.ts').LangContext.Provider,{value:lang},React.createElement(SudoInstallProvider,null,React.createElement(Button,{id:'server',label:'实验服务器'})));
 await act(async()=>{view=create(tree('en'));});assertEnglish(view,['实验服务器']);
 await act(async()=>view.root.findAllByType('button').find(n=>text(n)==='Prepare environment').props.onClick());assertEnglish(view,['实验服务器']);
 await act(async()=>view.update(tree('zh')));assert.match(text(view.toJSON()),/服务器环境安装/);
 await act(async()=>view.update(tree('en')));assertEnglish(view,['实验服务器']);await act(async()=>view.unmount());
});

test('login page and remembered-login errors are English',async()=>{
 const h=fixture({api:{hubRememberedLogin:async()=>{throw Error('fixture');}}}),View=h.get('AuthGate.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View));await settle();});assertEnglish(view);
 assert.match(text(view.toJSON()),/Welcome back/);await act(async()=>view.unmount());
});

test('app preference updates login/global context, document language, and persists without rewriting content',async()=>{
 const h=fixture(),{LocaleProvider,syncLanguage,useT}=h.get('i18n.ts');
 const Probe=()=>React.createElement('span',null,useT().t('知识库问答'));let view;
 await act(async()=>{view=create(React.createElement(LocaleProvider,null,React.createElement(Probe)));await settle();});
 assert.equal(text(view.toJSON()),'Knowledge Q&A');assert.equal(document.documentElement.lang,'en');
 await act(async()=>syncLanguage('zh'));assert.equal(text(view.toJSON()),'知识库问答');assert.equal(document.documentElement.lang,'zh-CN');
 await act(async()=>syncLanguage('en'));assert.equal(text(view.toJSON()),'Knowledge Q&A');assert.equal(localStorage.getItem('omaax.ui-language'),'en');await act(async()=>view.unmount());
});

test('real app and locale providers keep drafts mounted and ignore stale startup language after a preference change',async()=>{
 let reads=0,writes=0,mounts=0,unmounts=0,app;
 let settings={language:'zh',theme:'light',workspace:'C:/Project',mcpServers:[],onboarded:true};
 let resolveStartup;
 const startup=new Promise(resolve=>{resolveStartup=resolve;});
 const h=fixture({api:{
  getSettings:()=>++reads===2?startup:Promise.resolve(settings),
  setSettings:async patch=>{writes++;settings={...settings,...patch};return settings;},
  appInfo:async()=>({version:'0.4.50',platform:'win32',paths:{root:'C:/Data'}}),
  getCredential:async()=>({hasKey:true}),envStatus:async()=>({step:'idle',log:[]}),
  dshStatus:async()=>({state:'stopped',log:[]}),skillsCatalog:async()=>[],skillsStatuses:async()=>[],onDebugTab:()=>{},
 }});
 const {LocaleProvider,useT}=h.get('i18n.ts');
 const {AppProvider,useApp}=h.get('store.tsx');
 function Probe(){
  app=useApp();
  const [draft,setDraft]=React.useState('');
  React.useEffect(()=>{mounts++;return()=>{unmounts++;};},[]);
  return React.createElement('div',null,React.createElement('p',null,useT().t('知识库问答')),
   React.createElement('input',{value:draft,onChange:event=>setDraft(event.target.value)}));
 }
 let view;
 try{
  await act(async()=>{view=create(React.createElement(LocaleProvider,null,React.createElement(AppProvider,null,React.createElement(Probe))));await settle();});
  assert.equal(reads,2);assert.equal(app.ready,true);
  await act(async()=>view.root.findByType('input').props.onChange({target:{value:'自定义 数据 $& {name}'}}));
  await act(async()=>{await app.updateSettings({language:'en'});await settle();});
  assert.equal(text(view.toJSON()).trim(),'Knowledge Q&A');assert.equal(document.documentElement.lang,'en');
  await act(async()=>{resolveStartup({language:'zh'});await settle();});
  assert.equal(text(view.toJSON()).trim(),'Knowledge Q&A');assert.equal(document.documentElement.lang,'en');
  assert.equal(view.root.findByType('input').props.value,'自定义 数据 $& {name}');
  await act(async()=>{await app.updateSettings({language:'zh'});await settle();});
  assert.equal(text(view.toJSON()).trim(),'知识库问答');assert.equal(document.documentElement.lang,'zh-CN');
  assert.equal(view.root.findByType('input').props.value,'自定义 数据 $& {name}');
  assert.equal(localStorage.getItem('omaax.ui-language'),'zh');
  assert.deepEqual({reads,writes,mounts,unmounts},{reads:2,writes:2,mounts:1,unmounts:0});
 }finally{if(view)await act(async()=>view.unmount());}
 assert.equal(unmounts,1);
});

test('existing MCP validation messages switch language without resetting invalid drafts or reviving cleared errors',async()=>{
 const keys=['请输入字符串 JSON 数组','请输入字符串值的 JSON 对象'];
 const inputs=keys.map(key=>({dataset:{validationKey:key},validity:{customError:false},value:'',validationMessage:'',
  setCustomValidity(message){this.validationMessage=message;this.validity.customError=!!message;},reportValidity(){return !this.validity.customError;},
 }));
 let formMounts=0;
 const h=fixture({state:{settings:{language:'zh',theme:'light',workspace:'C:/Project',onboarded:true,
  mcpServers:[{name:'demo',transport:'stdio',command:'node',args:[],env:{}}],
 }}}),View=h.get('views/SettingsView.tsx').default;
 let view;
 try{
  await act(async()=>{view=create(h.wrap(View,'zh'),{createNodeMock:element=>{
   if(element.type==='form'){formMounts++;return {querySelectorAll:selector=>{assert.equal(selector,'input[data-validation-key]');return inputs;}};}
   return null;
  }});await settle();});
  const fields=keys.map(key=>view.root.findByProps({'data-validation-key':key}));
  await act(async()=>{
   inputs[0].value='["保留的路径",';inputs[1].value='{"CUSTOM":"保留的内容"';
   fields.forEach((field,index)=>field.props.onChange({target:inputs[index]}));
  });
  assert.deepEqual(inputs.map(input=>input.validationMessage),keys);
  await act(async()=>view.update(h.wrap(View,'en')));
  inputs.forEach(input=>{assert.doesNotMatch(input.validationMessage,han);assert.equal(input.validity.customError,true);});
  assert.deepEqual(inputs.map(input=>input.value),['["保留的路径",','{"CUSTOM":"保留的内容"']);
  keys.forEach((key,index)=>assert.equal(view.root.findByProps({'data-validation-key':key}),fields[index]));
  await act(async()=>{inputs[0].value='["保留的路径"]';fields[0].props.onChange({target:inputs[0]});});
  assert.equal(inputs[0].validationMessage,'');
  await act(async()=>view.update(h.wrap(View,'zh')));
  assert.equal(inputs[0].validationMessage,'');assert.equal(inputs[0].validity.customError,false);
  assert.equal(inputs[1].validationMessage,keys[1]);
  assert.deepEqual(inputs.map(input=>input.value),['["保留的路径"]','{"CUSTOM":"保留的内容"']);
  assert.equal(formMounts,1);
 }finally{if(view)await act(async()=>view.unmount());}
});

test('every literal translation key has a real English translation',()=>{
 const {translate}=fixture().get('i18n.ts'),missing=[];
 function visit(directory){for(const entry of fs.readdirSync(directory,{withFileTypes:true})){const file=path.join(directory,entry.name);if(entry.isDirectory()){visit(file);continue;}if(!/\.tsx?$/.test(file)||/i18n[.]/.test(file))continue;
 const source=ts.createSourceFile(file,fs.readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true,file.endsWith('.tsx')?ts.ScriptKind.TSX:ts.ScriptKind.TS);
 const walk=node=>{if(ts.isCallExpression(node)&&node.expression.getText(source)==='t'&&node.arguments[0]&&ts.isStringLiteralLike(node.arguments[0])){const key=node.arguments[0].text;if(han.test(translate('en',key)))missing.push({file:path.relative(root,file),line:source.getLineAndCharacterOfPosition(node.pos).line+1,key});}ts.forEachChild(node,walk);};walk(source);}}
 visit(path.join(root,'src/renderer/src'));assert.deepEqual(missing,[]);
});
