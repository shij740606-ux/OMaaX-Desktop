import type {Rect} from '@shared/types';

// Keep native layout calls ordered; only the latest pending rectangle matters.
export function dshLayoutQueue(apply:(rect:Rect,visible:boolean)=>Promise<unknown>,onError:(error:unknown)=>void){
 let pending:{rect:Rect;visible:boolean}|undefined,running=false;
 const flush=async()=>{
  running=true;
  try{while(pending){const next=pending;pending=undefined;try{await apply(next.rect,next.visible);}catch(error){onError(error);}}}
  finally{running=false;}
 };
 return (rect:Rect,visible:boolean)=>{pending={rect,visible};if(!running)void flush();};
}
