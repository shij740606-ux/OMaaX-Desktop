type Store=Pick<Storage,'getItem'|'setItem'|'removeItem'>;
const requests=new Map<string,Promise<unknown>>();

// Keep the request alive across page changes; preserve failures until the user retries.
export async function completePendingPlan<T>(storage:Store,key:string,taskId:string,create:(body:unknown)=>Promise<T>):Promise<T|null>{
 const raw=storage.getItem(key);if(!raw)return null;
 const pending=JSON.parse(raw);if(pending.id!==taskId)return null;
 if(pending.error)throw Error(pending.error);
 const flight=key+'\0'+raw;
 if(requests.has(flight))return requests.get(flight) as Promise<T>;
 const request=Promise.resolve().then(async()=>{
  try{
   const result=await create(pending.body);
   const value=result as {plan?:{id?:string};digest?:string};
   if(!value?.plan?.id||!value.digest)throw Error('服务器未返回有效的执行计划，请重试。');
   if(storage.getItem(key)===raw)storage.removeItem(key);
   return result;
  }catch(error){
   const message=error instanceof Error?error.message:String(error);
   if(storage.getItem(key)===raw)storage.setItem(key,JSON.stringify({...pending,error:message}));
   throw error;
  }finally{requests.delete(flight);}
 });
 requests.set(flight,request);return request;
}
