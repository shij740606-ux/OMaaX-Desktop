const clone=<T,>(value:T):T=>structuredClone(value);
const context={projectId:'qa-project',projectName:'测试项目',sessionId:'qa-session',sessionName:'测试对话'};
const rel='projects/qa-project/sessions/qa-session',root='/home/qa/omaax/workspace';
const image='sha256:'+'a'.repeat(64),setup={state:'complete',detail:'基础环境已就绪；分析工具按需配置'};
const now=()=>Date.now()/1000;
export function installFixtures(){
 const qa:any={calls:[],delays:{},failures:{},navigation:'',steps:[],prepareState:'idle',image,context};
 let next=3;
 let hosts:any[]=[{id:'qa-a',name:'测试服务器 A',host:'192.0.2.10',port:22,username:'qa',auth:'password',hostKey:'key-qa-a',fingerprint:'SHA256:qa-a',selected:true},{id:'qa-b',name:'测试服务器 B',host:'192.0.2.11',port:22,username:'qa',auth:'password',hostKey:'key-qa-b',fingerprint:'SHA256:qa-b',selected:false}];
 const files=new Map<string,any>(),trash:any[]=[],deletes:any[]=[],writes:any[]=[{id:'write-qa',path:'/qa/existing.txt',context,created:now(),digest:'write-digest',previous:{size:3},preview:'new content',proposedBytes:11}];
 const jobs:any[]=[{id:'job-complete',state:'succeeded',detail:'',created:now(),spec:{title:'测试已完成任务',context}},{id:'job-running',state:'running',detail:'',created:now(),spec:{title:'测试运行中任务',context}}];
 let transfers:any[]=[{id:'transfer-qa',source:'qa-a',target:'qa-b',state:'failed',created:now(),bytes:12,error:'ECONNRESET: socket hang up',destination:'/qa/import',progress:{bytes:6,total:12,current:'tiny.txt',completed:0,count:1,detail:'复制中断'}}];
 const add=(path:string,directory:boolean,text='')=>files.set(path,{name:path.split('/').at(-1),path:root+'/'+path,directory,size:directory?0:text.length,text});
 for(const category of ['tasks','configs','references','files'])add(rel+'/'+category,true);
 add(rel+'/configs/samples.json',false,'{"sample":"test"}');add(rel+'/files/note.txt',false,'test file content');add(rel+'/tasks/example',true);
 const record=async(name:string,id:string,body:any={})=>{
  const safe=clone(body);for(const field of ['password','privateKey','passphrase'])if(field in safe)safe[field]='[redacted]';qa.calls.push({name,id,body:safe,at:Date.now()});
  const key=name+':'+id,delay=qa.delays[key]??qa.delays[name]??0;if(delay)await new Promise(r=>setTimeout(r,delay));
  const failure=qa.failures[key]??qa.failures[name];if(failure)throw Error(failure);
 };
 const operation=async(id:string,action:string,body:any={})=>{
  await record(action,id,body);
  switch(action){
   case 'initialize':return {workspace:root,setup:qa.setupFailure?{state:'failed',detail:qa.setupFailure}:setup};
   case 'setup_start':case 'setup_status':return qa.setupFailure?{state:'failed',detail:qa.setupFailure}:setup;
   case 'install_environment':return {installed:true};
   case 'status':return {root:'/qa/control',workspace:root,sources:{shared:'/qa/shared'},images:[image],protection:{rootless:true},error:''};
   case 'jobs':return clone(jobs);
   case 'cancel':{const job=jobs.find(j=>j.id===body.id);if(job)job.state='cancelled';return {id:body.id};}
   case 'logs':return {text:'test task log: completed safely'};
   case 'audit':return [{id:1,at:now(),action:'job.succeeded',detail:JSON.stringify({id:'job-complete'})}];
   case 'server_environments':return {images:[{id:image,tags:['localhost/omaax-test:1'],size:1000000,installed:true,approved:true,bindings:[{workflow:'gene_family',default:true,steps:[]}],activeJobs:1}],error:'',setup,checkedAt:now(),conda:{environments:[{name:'existing-r',path:'/home/qa/miniconda3/envs/existing-r'}],error:''}};
   case 'workflow_readiness':qa.steps=clone(body.steps);return {steps:Object.fromEntries(body.steps.map((s:string)=>[s,{state:'ready',detail:'所需软件已检查'}]))};
   case 'workflow_prepare':qa.prepareState='running';qa.steps=clone(body.steps);return {id:'prepare-qa',state:'running',detail:'正在配置所选步骤',request:{steps:qa.steps}};
   case 'workflow_prepare_status':return {id:'prepare-qa',state:qa.prepareState,detail:qa.prepareState==='complete'?'所选步骤配置完成':qa.prepareState==='running'?'正在配置所选步骤':'尚未配置',request:{steps:qa.steps},log:qa.prepareState==='complete'?'test installation log':''};
   case 'workflow_environment':return {ready:true};
   case 'server_delete_requests':return clone(deletes);
   case 'server_delete_reject':{const i=deletes.findIndex(d=>d.id===body.id);if(i>=0)deletes.splice(i,1);return {rejected:true};}
   case 'server_delete_confirm':{const i=deletes.findIndex(d=>d.id===body.id);if(i>=0){const row=deletes.splice(i,1)[0];files.delete(row.relative);trash.push({trashId:row.id,path:root+'/'+row.relative,deleted:now(),entry:row.entry});}return {deleted:true};}
   case 'server_write_requests':return clone(writes);
   case 'server_write_confirm':case 'server_write_reject':{const i=writes.findIndex(d=>d.id===body.id);if(i>=0)writes.splice(i,1);return action==='server_write_confirm'?{backup:'/qa/overwrite-backup.txt'}:{rejected:true};}
   case 'workspace_restore':{const item=trash.find(t=>t.trashId===body.trashId);if(item){files.set(item.path.slice(root.length+1),item.entry);trash.splice(trash.indexOf(item),1);}return {restored:true};}
   case 'server_files':return {text:'readonly external file',files:[]};
   case 'workspace_files':{
    const path=body.path||'files',op=body.operation||'list';
    if(op==='info')return {workspaceRoot:root,path:root};
    if(op==='contexts')return {items:[{path:rel,context,updated:now()}]};
    if(op==='external')return {files:[{path:'/qa/external.txt',context,created:now()}]};
    if(op==='trash')return {files:clone(trash)};
    if(op==='read'){if(!files.has(path))throw Error('ENOENT: no such file');return {text:files.get(path).text};}
    if(op==='mkdir'){if(files.has(path))throw Error('目标位置已有文件，不能覆盖');add(path,true);return {path:root+'/'+path};}
    if(op==='rename'||op==='move'){
     if(files.has(body.destination))throw Error('目标位置已有文件，不能覆盖');
     for(const [name,item] of [...files])if(name===path||name.startsWith(path+'/')){const destination=body.destination+name.slice(path.length);files.delete(name);files.set(destination,{...item,path:root+'/'+destination,name:destination.split('/').at(-1)});}return {path:root+'/'+body.destination,moved:true};
    }
    if(op==='delete'){
     const item=files.get(path);if(!item)throw Error('文件不存在');
     if(item.directory){deletes.push({id:'delete-'+next++,relative:path,entry:clone(item),context,created:now(),digest:'delete-digest',paths:[root+'/'+path],warnings:['本次使用递归删除，请核对范围'],snapshot:{entries:1,bytes:0}});return {requiresApproval:true};}
     const token='trash-'+next++;trash.push({trashId:token,path:root+'/'+path,deleted:now(),entry:clone(item)});files.delete(path);return {recoverable:true,trashId:token};
    }
    let rows=[...files.entries()].filter(([name,item])=>op==='search'?name.startsWith(path+'/')&&item.name.includes(body.query):name.startsWith(path+'/')&&!name.slice(path.length+1).includes('/')).map(([,value])=>clone(value));
    return {path:root+'/'+path,files:rows,nextOffset:null};
   }
   case 'transfers':return clone(transfers);
   case 'transfer_resume':{const row=transfers.find(t=>t.id===body.id);if(row){row.state='complete';row.error='';row.progress.bytes=row.progress.total;row.progress.completed=1;}return {path:'/qa/import'};}
   case 'transfer':{transfers.push({id:'transfer-'+next++,source:id,target:body.target,state:'complete',created:now(),bytes:12,destination:'/qa/copied'});return {path:'/qa/copied',files:1,source:'import_qa'};}
   case 'configure':return {saved:true};
   default:throw Error('Unimplemented QA fixture action: '+action);
  }
 };
 (window as any).__qa=qa;
 (window as any).biodsh={
  sshHosts:async()=>clone(hosts),
  sshHostSave:async(draft:any)=>{await record('save',draft.id||'',draft);const old=hosts.find(h=>h.id===draft.id),id=old?.id||'qa-'+next++;const row={...old,...draft,id,selected:old?.selected||false,hostKey:old?.host===draft.host?old.hostKey:null};delete row.password;delete row.privateKey;delete row.passphrase;hosts=hosts.filter(h=>h.id!==id).concat(row);return clone(hosts);},
  sshHostRemove:async(id:string)=>{await record('remove',id);hosts=hosts.filter(h=>h.id!==id);return clone(hosts);},
  sshHostTrust:async(id:string,hostKey:string,fingerprint:string)=>{await record('trust',id);hosts=hosts.map(h=>h.id===id?{...h,hostKey,fingerprint}:h);return clone(hosts);},
  sshHostSelect:async(id:string,selected:boolean)=>{await record('select',id,{selected});hosts=hosts.map(h=>h.id===id?{...h,selected}:h);return clone(hosts);},
  sshHostTest:async(id:string,probe:boolean)=>{await record(probe?'probe':'authenticate',id);return {hostKey:'key-'+id,fingerprint:'SHA256:'+id,connected:true};},
  sshHostMetrics:async(id:string)=>{await record('metrics',id);return {cpuModel:'QA CPU',cpuPercent:15,logicalCores:32,physicalCores:16,memoryTotal:64*1073741824,memoryUsed:8*1073741824,swapTotal:4*1073741824,swapUsed:0,load:[.1,.2,.3],disks:[{mount:'/',total:100*1073741824,used:10*1073741824,free:90*1073741824}],hostname:id,system:'Linux',architecture:'x86_64',uptimeSeconds:86400,sampledAt:now()};},
  sshHostFiles:async(id:string,body:any)=>{
   await record('files',id,body);const path=!body.path||body.path==='~'?'/home/qa':body.path;
   if(path==='/denied')throw Error('Permission denied');if(path==='/missing')throw Error('ENOENT: no such file or directory');
   let rows=path==='/home/qa'?[{name:'data',directory:true},{name:'.hidden',directory:true},...Array.from({length:430},(_,i)=>({name:'文件 '+i+'.txt',directory:false}))]:path==='/home/qa/data'?[{name:'tiny.txt',directory:false}]:[];
   rows=rows.filter(r=>body.showHidden||!r.name.startsWith('.'));const offset=body.offset||0,limit=body.limit||200;
   return {path,home:'/home/qa',parent:path==='/'?null:path.split('/').slice(0,-1).join('/')||'/',files:rows.slice(offset,offset+limit).map(row=>({...row,path:path+'/'+row.name,link:false,size:12,modified:now(),permissions:'644'})),nextOffset:offset+limit<rows.length?offset+limit:null};
  },
  personalCompute:operation,
  dshRpc:async(method:string,body:any)=>{await record(method,'',body);return method==='server.context'?context:{queued:true};}
 };
}
