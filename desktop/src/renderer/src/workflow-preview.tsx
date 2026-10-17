// Test-only HTML entry. No credentials, model requests or real servers are used here.
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';
import catalog from '../../../resources/workflows/catalog.json';
const settings={onboarded:true,language:'zh',theme:'light',mode:'local'};
const host={id:'preview-175',name:'175（界面验收）',host:'example.invalid',username:'test',hostKey:'test',selected:true};
type MockPlan={plan:Record<string,unknown>;digest:string;confirmed:boolean;state:string;detail:string};
let plans:MockPlan[]=[];
const environmentTasks:Record<string,{id:string;state:string;detail:string;log:string;request:{steps:string[]}}>={};
const scenario=new URLSearchParams(location.search).get('failure');
window.biodsh={
  appInfo:async()=>({version:'0.4.3',platform:'win32',dark:false,paths:{root:'C:/isolated-ui-preview'}}),
  ratingsGet:async()=>({}),ratingsSet:async()=>({}),getSettings:async()=>settings,setSettings:async()=>settings,getCredential:async()=>({hasKey:true}),
  envStatus:async()=>({ready:true,step:'ready',progress:100,message:'',log:[]}),
  dshStatus:async()=>({state:'running',log:[]}),skillsCatalog:async()=>[],skillsStatuses:async()=>[],
  onEvent:()=>()=>{},onDebugTab:(cb:(tab:string)=>void)=>{setTimeout(()=>cb('workflows'),100);},
  deepseekBalance:async()=>({balance_infos:[]}),dshSetContext:async()=>{},gitStatus:async()=>({available:false,isRepo:false,dirty:0}),
  dshRpc:async(method:string)=>method==='workspace.list'?{items:[{workspaceId:'preview',path:'C:/isolated-ui-preview/project',title:'界面验收',sessionIds:[],updatedAt:''}],archivedSessionIds:[]}:method==='session.list'?{items:[]}:{},
  dshNewSession:async()=> 'preview-session',dshOpenSession:async()=>{},dshBounds:()=>{},windowControl:async()=>{},
  sshHosts:async()=>[host],personalCompute:async(_id:string,action:string,body:unknown={})=>{
    if(action==='workflow_catalog')return catalog;
    if(action==='workflow_inbox')return null;
    if(action==='status')return {sources:{pilot:'仅验收目录'},images:['sha256:test']};
    if(action==='workflow_plans')return plans;
    if(action==='workflow_validate'){if(scenario==='plan')throw Error('请填写样本信息表，再生成计划。');return {valid:true};}
    if(action==='workflow_prepare'){
      const b=body as {workflow:string;steps:string[]},task={id:'environment-'+b.workflow,state:'running',detail:'正在配置所选步骤需要的软件',log:'只检查本次选择的步骤，不安装其他工具。',request:{steps:b.steps}};environmentTasks[b.workflow]=task;
      setTimeout(()=>{task.state=scenario==='install'?'failed':'complete';task.detail=scenario==='install'?'服务器下载连接失败，已有环境保持不变。':'所选步骤的环境已准备完成';},1500);return task;
    }
    if(action==='workflow_prepare_status')return environmentTasks[(body as {workflow:string}).workflow]||{state:'idle',detail:'选择步骤后，检查并配置所需环境。'};
    if(action==='workflow_environment')return {ready:true};
    if(action==='workflow_readiness'){
      const id=(body as {workflow:string}).workflow,f=catalog.workflows.find(f=>f.id===id)!;
      return {steps:Object.fromEntries([...f.nodes,...f.extraNodes].map(s=>[s.id,{state:!s.entryPresent?'missing_source':s.id==='rnaseq_wgcna'&&environmentTasks[id]?.state!=='complete'?'missing_environment':'ready',missing:s.id==='rnaseq_wgcna'?['R 包 WGCNA']:[],detail:s.id==='rnaseq_wgcna'?'缺少：R 包 WGCNA':'软件已检查'}]))};
    }
    if(action==='workflow_plan'){
      const b=body as Record<string,unknown>,f=catalog.workflows.find(f=>f.id===b.workflow)!;
      const p={plan:{id:'preview-'+f.id,workflow:f.id,name:f.name,version:catalog.version,source:b.source,input:b.input,prefix:b.prefix,steps:b.steps||f.defaultSteps,parameters:b.parameters||{},configs:b.configs||{},resources:{cpu:b.cpu||1,memoryMb:b.memoryMb||2048,timeoutSeconds:b.timeoutSeconds||86400},inputs:{'samples.csv':{size:128}},software:{python:'3.12'}},digest:'preview-digest',confirmed:false,state:'awaiting_confirmation',detail:''};plans=[p,...plans.filter(old=>old.plan.id!==p.plan.id)];return p;
    }
    if(action==='workflow_confirm'){const id=(body as {id:string}).id;const p=plans.find(p=>p.plan.id===id)!;p.confirmed=true;p.state='queued';setTimeout(()=>{if(p.state==='queued')p.state='complete';},1500);return {};}
    if(action==='cancel'){const p=plans.find(p=>p.plan.id===(body as {id:string}).id)!;p.state='cancelled';return {};}
    if(action==='workflow_result'){const p=plans.find(p=>p.plan.id===(body as {id:string}).id)!;return {id:p.plan.id,name:p.plan.name,state:p.state,version:catalog.version,steps:(p.plan.steps as string[]).map(id=>({id,state:'complete'})),files:[{path:'analysis/preview/result.tsv',size:512,sha256:'mock'}],fileCount:1};}
    if(action==='logs')return {text:'隔离界面验收：此日志为模拟内容，不连接服务器。'};
    if(action==='transfer')return {path:'C:/isolated-ui-preview/result'};
    throw Error('隔离预览不支持此操作');
  },
} as unknown as typeof window.biodsh;
// Extended model-workflow acceptance scenario, still entirely synthetic.
if(new URLSearchParams(location.search).has('model')){
 const qa={calls:[] as {action:string;body:any}[],failure:false,delay:0};(window as any).__qa=qa;
 const api=window.biodsh,oldCompute=api.personalCompute;
 api.workspaceFiles=async()=>[];
 api.sshHosts=async()=>[host,{...host,id:'preview-b',name:'空服务器'}] as any;
 api.dshRpc=async(method:string,args:any)=>{qa.calls.push({action:method,body:args});if(method==='workspace.list')return {items:[{workspaceId:'preview',path:'C:/isolated-ui-preview/project',title:'界面验收',sessionIds:['preview-session'],updatedAt:''}],archivedSessionIds:[]};if(method==='session.list')return {items:[]};if(method==='server.context')return {projectId:'preview',projectName:'测试项目',sessionId:'preview-session',sessionName:'测试对话'};return {};};
 const record=(id:string,state:string)=>({plan:{id,prefix:id,name:'RNA-seq',context:{projectName:'测试项目',sessionName:'测试对话'}},state});
 api.personalCompute=async(id:string,action:string,body:any={})=>{
  qa.calls.push({action,body});if(qa.delay)await new Promise(resolve=>setTimeout(resolve,qa.delay));
  if(action==='workflow_plans'){if(qa.failure)throw Error('模拟连接失败');return id==='preview-b'?[]:[record('已完成任务','complete'),record('失败任务','failed'),record('运行任务','running')];}
  if(action==='workflow_result'){
   const files=[{path:'report.html',size:120},{path:'table.tsv',size:30},{path:'plot.png',size:68}];
   const result:any={id:body.id,state:body.id==='失败任务'?'failed':'complete',error:body.id==='失败任务'?'示例错误，保留已生成文件':undefined,steps:[{id:'rnaseq_qc',state:'complete'}],files:body.offset?[{path:'last.txt',size:10}]:files,fileCount:4,nextOffset:body.offset?null:3};
   if(body.file==='report.html')result.content='<h1>结果报告</h1><script>parent.__reportExecuted=true</script><img src="https://example.invalid/should-not-load">';
   if(body.file==='table.tsv')result.content='gene\tvalue\nA\t1';
   if(body.file==='plot.png'){result.mime='image/png';result.contentBase64='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';}
   if(body.file==='last.txt')result.content='最后一个文件';result.contentFile=body.file;return result;
  }
  return oldCompute(id,action,body);
 };
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
