import type {ResearchAnalysis} from './research-types';
import type {ResearchGraphLayout} from './research-graph-model';
import ELK from 'elkjs/lib/elk-api';
import {routeResearchGraph} from './research-graph-routing';
export {RESEARCH_NODE_WIDTH,RESEARCH_NODE_HEIGHT,type ResearchGraphLayout} from './research-graph-model';

export function layoutResearchGraph(analysis:ResearchAnalysis,signal?:AbortSignal):Promise<ResearchGraphLayout>{
 return new Promise((resolve,reject)=>{
  if(signal?.aborted){reject(new DOMException('Cancelled','AbortError'));return;}
  const worker=new Worker(new URL('./research-graph-layout.worker.ts',import.meta.url),{type:'module'});
  let settled=false;
  const finish=(value?:ResearchGraphLayout,error?:Error)=>{
   if(settled)return;settled=true;clearTimeout(timeout);signal?.removeEventListener('abort',cancel);worker.terminate();
   if(error)reject(error);else resolve(value!);
  };
  const cancel=()=>finish(undefined,new DOMException('Cancelled','AbortError'));
  const timeout=setTimeout(()=>finish(undefined,Error('Research layout timed out')),30_000);
  worker.onerror=event=>{event.preventDefault();finish(undefined,Error('Research layout worker failed'));};
  worker.onmessageerror=()=>finish(undefined,Error('Research layout response failed'));
  signal?.addEventListener('abort',cancel,{once:true});
  try{
   const engine=new ELK({workerFactory:()=>worker});
   void routeResearchGraph(analysis,engine).then(value=>{
    const valid=analysis.nodes.every(node=>Number.isFinite(value.positions[node.id]?.x)&&Number.isFinite(value.positions[node.id]?.y))&&analysis.edges.every(edge=>!!value.paths[edge.id]);
    finish(valid?value:undefined,valid?undefined:Error('Research layout failed'));
   }).catch(()=>finish(undefined,Error('Research layout failed')));
  }catch{finish(undefined,Error('Research layout could not start'));}
 });
}
