import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import {root,React,act,settle,text,han,fixture,assertEnglish} from './locale-test-utils.mjs';
const {create}=createRequire(import.meta.url)('react-test-renderer');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'resources/workflows/catalog.json'),'utf8'));
const host={id:'server',name:'我的实验服务器',host:'127.0.0.1',username:'tester',port:22,auth:'password',hostKey:'fixture',selected:true};
const metrics={sampledAt:1700000000,cpuPercent:25,logicalCores:8,physicalCores:4,cpuModel:'CPU',memoryUsed:1e9,memoryTotal:8e9,swapUsed:0,swapTotal:1e9,hostname:'lab',system:'Linux',architecture:'x64',uptimeSeconds:86400,load:[1,2,3],disks:[{mount:'/数据',used:1e9,total:8e9,free:7e9}],gpu:''};
async function rendered(h,View,props={}){let view;await act(async()=>{view=create(h.wrap(View,'en',props));await settle();});return view;}

test('all workflow catalog display copy and parameter option labels have English translations',()=>{
 const h=fixture(),{translate}=h.get('i18n.ts'),missing=[];
 const walk=value=>{if(!value||typeof value!=='object')return;for(const [key,item]of Object.entries(value)){
  if(['name','label','description','help','necessity','section','software'].includes(key)&&typeof item==='string'&&han.test(item)&&han.test(translate('en',item)))missing.push(item);
  else if(key==='substeps'){for(const text of item||[])if(han.test(translate('en',text)))missing.push(text);}
  else walk(item);
 }};walk(catalog);
 const {parameterNames,parameterHints,parameterCopy,optionLabel}=h.get('views/workflowParameterHelp.ts');
 for(const s of [...Object.values(parameterNames),...Object.values(parameterHints)])if(han.test(translate('en',s)))missing.push(s);
 for(const key of Object.keys(parameterNames)){const copy=parameterCopy(key,'fixture',{},'');for(const value of [copy.name,copy.hint,copy.necessity])if(han.test(translate('en',value)))missing.push(value);}
 for(const flow of catalog.workflows)for(const step of [...flow.nodes,...flow.extraNodes])for(const argument of step.arguments||[])for(const choice of argument.choices||[])if(han.test(translate('en',optionLabel(choice,argument.key))))missing.push(optionLabel(choice,argument.key));
 assert.deepEqual([...new Set(missing)],[]);
});

test('server business errors translate nested workflow metadata without changing input paths or sample names',()=>{
 const h=fixture(),{translate}=h.get('i18n.ts'),{serverDisplayMessage}=h.get('serverDisplay.ts');
 const t=(source,vars)=>translate('en',source,vars);
 const messages=[
  '输入路径无法读取，链接可能失效或循环：/原始数据/样本一.fastq',
  '样本 实验样本一 缺少分组信息，请补齐 condition 或 groups',
  '检查测序质量：正在配置 Conda 环境，日志保留在服务器',
  '检查测序质量尚不能运行：缺少：R 包 DESeq2、R 包 tximport。请先检查并配置所选步骤。',
  '删除范围或内容已变化，请重新提交；未删除任何文件',
  '凭据保存后无法读取，原有凭据已保留，请检查系统安全存储',
  '所选 3 个步骤的环境已准备完成',
 ];
 for(const source of messages){const english=serverDisplayMessage(t,source);assert.equal(han.test(english.replace('/原始数据/样本一.fastq','').replace('实验样本一','')),false,english);assert.equal(serverDisplayMessage((source)=>source,source),source);}
 assert.match(serverDisplayMessage(t,messages[0]),/\/原始数据\/样本一.fastq$/);
 assert.match(serverDisplayMessage(t,messages[3]),/R package DESeq2, R package tximport/);
});

test('resource cards translate labels and dynamic totals while retaining host paths',async()=>{
 const h=fixture(),View=h.get('views/HostsView.tsx').ResourceSummary,view=await rendered(h,View,{value:metrics});
 assertEnglish(view,['数据']);assert.match(text(view.toJSON()),/logical cores/);assert.match(text(view.toJSON()),/1.0 days/);
 await act(async()=>view.update(h.wrap(View,'zh',{value:metrics})));assert.match(text(view.toJSON()),/逻辑核/);
 await act(async()=>view.update(h.wrap(View,'en',{value:metrics})));assertEnglish(view,['数据']);await act(async()=>view.unmount());
});

test('protected paths and editable drafts survive live language switches without changing path data',async()=>{
 const h=fixture({api:{personalCompute:async()=>[{path:'/原始数据',resolved:'/真实路径',directory:true}]}}),View=h.get('views/ProtectedPaths.tsx').default,props={server:'server'},view=await rendered(h,View,props);
 assertEnglish(view,['原始数据','真实路径']);assert.match(text(view.toJSON()),/Resolved path/);
 await act(async()=>view.root.findByType('input').props.onChange({target:{value:'/我的新增数据'}}));
 await act(async()=>view.update(h.wrap(View,'zh',props)));assert.match(text(view.toJSON()),/保护原始数据/);
 await act(async()=>view.update(h.wrap(View,'en',props)));assertEnglish(view,['原始数据','真实路径']);assert.equal(view.root.findByType('input').props.value,'/我的新增数据');await act(async()=>view.unmount());
});

test('parameter help, required state, choices and validation errors follow the active language',async()=>{
 const h=fixture(),View=h.get('views/WorkflowParameterField.tsx').default;let value;
 const props={name:'powers',stepId:'wgcna',value:[1,2],schema:{type:'array',items:{type:'number'}},onChange:v=>value=v};
 const view=await rendered(h,View,props);assertEnglish(view);
 await act(async()=>view.root.findByType('input').props.onChange({target:{value:'invalid'}}));assertEnglish(view);assert.match(text(view.toJSON()),/numeric values/);assert.equal(value,undefined);
 await act(async()=>view.update(h.wrap(View,'zh',props)));assert.match(text(view.toJSON()),/请填写数值/);
 await act(async()=>view.update(h.wrap(View,'en',props)));assertEnglish(view);assert.equal(view.root.findByType('input').props.value,'invalid');await act(async()=>view.unmount());
});

test('environment inventory, workflow step labels and setup statuses translate with no setup side effects',async()=>{
 const calls=[];
 const h=fixture({api:{personalCompute:async(_server,action)=>{calls.push(action);if(action==='server_environments')return {images:[{id:'env-id',tags:['Conda 分析环境'],size:1e9,installed:true,approved:true,bindings:[{workflow:catalog.workflows[0].id,default:true,steps:[]}],activeJobs:2}],error:'',setup:{state:'complete',detail:'基础环境已就绪；分析工具在使用时按需配置'},checkedAt:Date.now()/1000,conda:{environments:[{name:'my-env',path:'/我的环境'}],error:''}};return {state:'idle',detail:'勾选需要的步骤后再检查或配置，不会自动安装其他工具。'};}}}),View=h.get('components/ServerEnvironments.tsx').default,props={server:'locale-test-env',label:host.name},view=await rendered(h,View,props);
 assertEnglish(view,['我的实验服务器','我的环境']);assert.match(text(view.toJSON()),/Gene family analysis/);assert.match(text(view.toJSON()),/2 active tasks/);
 await act(async()=>view.update(h.wrap(View,'zh',props)));assert.match(text(view.toJSON()),/基因家族分析/);
 await act(async()=>view.update(h.wrap(View,'en',props)));assertEnglish(view,['我的实验服务器','我的环境']);assert.ok(calls.every(action=>['server_environments','workflow_prepare_status'].includes(action)));await act(async()=>view.unmount());
});

test('file browser translates controls and copy feedback without renaming Chinese files',async()=>{
 const h=fixture({api:{sshHostFiles:async()=>({path:'/数据',parent:'/',home:'/home/test',files:[{name:'原始数据.csv',path:'/数据/原始数据.csv',directory:false,size:10,modified:1700000000,permissions:'rw-r--r--'}],nextOffset:null})}}),View=h.get('components/ServerFileBrowser.tsx').default,props={server:'server'},view=await rendered(h,View,props);
 assertEnglish(view,['原始数据','数据']);assert.match(text(view.toJSON()),/Server files/);await act(async()=>view.update(h.wrap(View,'zh',props)));assert.match(text(view.toJSON()),/服务器文件/);
 await act(async()=>view.update(h.wrap(View,'en',props)));assertEnglish(view,['原始数据','数据']);await act(async()=>view.unmount());
});

test('workflow overview displays all flow titles and descriptions in English',async()=>{
 const h=fixture({api:{sshHosts:async()=>[],personalCompute:async()=>null}}),View=h.get('views/WorkflowsView.tsx').default,view=await rendered(h,View);
 assertEnglish(view);assert.match(text(view.toJSON()),/Gene family analysis/);assert.match(text(view.toJSON()),/Spatial transcriptomics/);
 await act(async()=>view.update(h.wrap(View,'zh')));assert.match(text(view.toJSON()),/基因家族分析/);
 await act(async()=>view.update(h.wrap(View,'en')));assertEnglish(view);await act(async()=>view.unmount());
});

test('file operation errors are translated and retain the exact protected path',()=>{
 const h=fixture(),{translate}=h.get('i18n.ts');
 assert.equal(translate('en','原始数据保护：不能写入、覆盖、移动或删除此路径：/实验数据'),'Raw data protection: this path cannot be written, overwritten, moved or deleted: /实验数据');
 assert.equal(translate('en','路径已复制：/实验数据/$&'),'Path copied: /实验数据/$&');
 assert.equal(translate('en','基础环境已就绪；分析工具在使用时按需配置'),'Base environment ready. Analysis tools are configured as needed.');
});

test('server tasks and audit events use localized state labels without changing task titles',async()=>{
 const h=fixture({api:{sshHosts:async()=>[host],personalCompute:async(_server,action)=>{
  if(action==='status')return {root:'/data',sources:{shared:'/data'},images:[],protection:true};
  if(action==='jobs')return [{id:'job',created:1700000000,state:'running',spec:{title:'我的任务',context:{projectName:'我的项目',sessionName:'我的对话'}}}];
  if(action==='audit')return [{id:'event',at:1700000000,action:'job.complete',detail:'基础环境已就绪；分析工具在使用时按需配置'}];
  if(action==='workspace_files')return {path:'/data',workspaceRoot:'/data',files:[],items:[]};
  return [];
 }}}),View=h.get('views/PersonalComputeView.tsx').default,view=await rendered(h,View,{section:'tasks'});
 assertEnglish(view,['我的实验服务器','我的任务','我的项目','我的对话']);assert.match(text(view.toJSON()),/Analyzing/);
 await act(async()=>{view.update(h.wrap(View,'en',{section:'protection'}));await settle();});assertEnglish(view,['我的实验服务器']);assert.match(text(view.toJSON()),/Task Completed/);
 await act(async()=>view.update(h.wrap(View,'zh',{section:'protection'})));assert.match(text(view.toJSON()),/保护原始数据/);
 await act(async()=>view.update(h.wrap(View,'en',{section:'protection'})));assertEnglish(view,['我的实验服务器']);await act(async()=>view.unmount());
});

test('workflow result records and resumed-run feedback use the current locale',async()=>{
 const h=fixture({api:{sshHosts:async()=>[host],personalCompute:async(_server,action)=>action==='workflow_plans'?[{plan:{id:'result',prefix:'rna',name:'RNA-seq 分析',context:{projectName:'我的项目'}},state:'complete',detail:'流程已提交，可查看执行进度。'}]:{id:'result',state:'complete',files:[],steps:[],fileCount:0}}}),View=h.get('components/WorkflowResults.tsx').default,view=await rendered(h,View);
 assertEnglish(view,['我的实验服务器','我的项目']);await act(async()=>view.root.findAllByType('button').find(n=>text(n)==='View results').props.onClick());await act(settle);assertEnglish(view,['我的实验服务器','我的项目']);
 await act(async()=>view.update(h.wrap(View,'zh')));assert.match(text(view.toJSON()),/RNA-seq 分析/);await act(async()=>view.update(h.wrap(View,'en')));assertEnglish(view,['我的实验服务器','我的项目']);await act(async()=>view.unmount());
});
