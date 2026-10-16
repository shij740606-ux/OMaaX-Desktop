import {useSyncExternalStore} from 'react';
import {serverError} from './serverErrors';
export type Setup={state:'idle'|'running'|'complete'|'failed'|'interrupted';detail:string;updated?:number;errorDetail?:string};
export type Connection={network:'unknown'|'testing'|'verified'|'failed';authentication:'unknown'|'awaiting_trust'|'checking'|'verified'|'failed';checkedAt?:number;error?:string;workspace?:string;setup?:Setup};
let snapshot:Record<string,Connection>={};
const listeners=new Set<()=>void>(),preparing=new Map<string,Promise<void>>();
const revisions=new Map<string,number>();
export const connectionRevision=(id:string)=>revisions.get(id)||0;
export function resetConnection(id:string){revisions.set(id,connectionRevision(id)+1);preparing.delete(id);snapshot={...snapshot,[id]:{network:'unknown',authentication:'unknown'}};listeners.forEach(fn=>fn());}
export const connectionState=(id:string)=>snapshot[id];
export function updateConnection(id:string,value:Partial<Connection>){
 if(value.setup&&['failed','interrupted'].includes(value.setup.state)){const raw=value.setup.errorDetail||value.setup.detail;value={...value,setup:{...value.setup,detail:serverError(raw,'environment'),errorDetail:raw}};}
 if(value.error)value={...value,error:serverError(value.error,'connection')};
 snapshot={...snapshot,[id]:{...(snapshot[id]||{network:'unknown',authentication:'unknown'}),...value}};listeners.forEach(fn=>fn());}
export const useServerConnections=()=>useSyncExternalStore(fn=>{listeners.add(fn);return()=>{listeners.delete(fn);};},()=>snapshot);
export function prepareServer(id:string,retry=false,image?:string):Promise<void>{
 const existing=preparing.get(id);if(existing)return existing;
 const revision=connectionRevision(id),current=()=>revision===connectionRevision(id);
 const work=(async()=>{
  updateConnection(id,{setup:{state:'running',detail:'正在创建 ~/omaax 并检查基础环境'}});
  try{
   const initialized=await window.biodsh.personalCompute(id,'initialize') as {workspace:string;setup:Setup};
   if(!current())return;
   updateConnection(id,{workspace:initialized.workspace,setup:initialized.setup});
   const setup=await window.biodsh.personalCompute(id,'setup_start',{retry,...(image?{image}:{})}) as Setup;
   if(current())updateConnection(id,{setup});
  }catch(reason){if(current())updateConnection(id,{setup:{state:'failed',detail:String(reason)}});}
 })();preparing.set(id,work);void work.finally(()=>{if(preparing.get(id)===work)preparing.delete(id);});return work;
}
let polling=false;
setInterval(()=>{if(polling)return;polling=true;void Promise.allSettled(Object.entries(snapshot).filter(([,v])=>v.setup?.state==='running').map(async([id])=>{
 const revision=connectionRevision(id);
 try{const setup=await window.biodsh.personalCompute(id,'setup_status') as Setup;if(revision!==connectionRevision(id))return;updateConnection(id,{setup});if(setup.state==='complete')window.dispatchEvent(new CustomEvent('biodsh-install-finished',{detail:{id}}));}
 catch(reason){if(revision===connectionRevision(id))updateConnection(id,{setup:{state:'interrupted',detail:'环境配置状态暂时无法查询，重新连接后可以继续检查。',errorDetail:String(reason)}});}
})).finally(()=>{polling=false;});},5000);
