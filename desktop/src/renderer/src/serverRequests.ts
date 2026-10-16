// One bounded request queue for management pages; mutations are never automatically retried.
const reads=new Set(['status','jobs','audit','setup_status','server_environments','ssh_files','workflow_prepare_status','workflow_readiness','server_write_requests','server_delete_requests','transfers']);
const pending=new Map<string,Promise<unknown>>(),cache=new Map<string,{value:unknown;until:number}>();
const epochs=new Map<string,number>();
const control={active:0,limit:4,waiting:[] as Array<()=>void>};
const bulk={active:0,limit:2,waiting:[] as Array<()=>void>};
export function invalidateServer(id:string){epochs.set(id,(epochs.get(id)||0)+1);for(const key of cache.keys())if(key.startsWith(id+'|'))cache.delete(key);}
export async function serverRequest<T>(id:string,action:string,body:unknown,execute:()=>Promise<T>):Promise<T>{
 const operation=body&&typeof body==='object'?(body as Record<string,unknown>).operation:undefined;
 const read=reads.has(action)||action==='metrics'||action==='server_files'||action==='workspace_files'&&['info','contexts','external','list','read','stat','search','trash'].includes(String(operation)),epoch=epochs.get(id)||0,key=id+'|'+action+'|'+JSON.stringify(body)+'|'+epoch;
 if(read){const saved=cache.get(key);if(saved&&saved.until>Date.now())return saved.value as T;const existing=pending.get(key);if(existing)return existing as Promise<T>;}
 else invalidateServer(id);
 const lane=['transfer','transfer_resume'].includes(action)?bulk:control;
 const work=(async()=>{
  if(lane.active>=lane.limit)await new Promise<void>(resolve=>lane.waiting.push(resolve));else lane.active++;
  try{const value=await execute();if(read&&(epochs.get(id)||0)===epoch){if(cache.size>256)cache.delete(cache.keys().next().value!);cache.set(key,{value,until:Date.now()+(action==='metrics'?15000:3000)});}return value;}
  finally{const next=lane.waiting.shift();if(next)next();else lane.active--;}
 })();
 if(read)pending.set(key,work);
 try{return await work;}finally{if(pending.get(key)===work)pending.delete(key);}
}
