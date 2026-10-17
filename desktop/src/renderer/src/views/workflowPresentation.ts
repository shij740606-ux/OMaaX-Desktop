export type WorkflowStep = {id:string;entryPresent:boolean};
export type StepReadiness = {state:'ready'|'missing_environment'|'unchecked'|'missing_source';missing?:string[];detail?:string};

export function orderedSteps<T extends WorkflowStep>(flow:{nodes:T[];extraNodes:T[];order:string[]}):T[] {
 const rank=new Map(flow.order.map((id,i)=>[id,i]));
 return [...flow.nodes,...flow.extraNodes].sort((a,b)=>(rank.get(a.id)??Number.MAX_SAFE_INTEGER)-(rank.get(b.id)??Number.MAX_SAFE_INTEGER));
}

export function workflowConnections<T extends WorkflowStep & {autoInputs?:Record<string,{steps:string[]}>}>(steps:T[],selected:string[]) {
 const available=steps.filter(s=>s.entryPresent);
 return available.flatMap((step,index)=>{
  const earlier=new Set(available.slice(0,index).map(s=>s.id));
  const producers=Object.values(step.autoInputs||{}).map(input=>available.slice(0,index).filter(s=>selected.includes(s.id)&&input.steps.includes(s.id)).at(-1)?.id).filter((id):id is string=>!!id&&earlier.has(id));
  return Array.from(new Set([...producers,...(index?[available[index-1].id]:[])])).map(id=>({id:'e-'+id+'-'+step.id,source:id,target:step.id,type:'smoothstep'}));
 });
}

export function stepReadiness(step:WorkflowStep,checks:Record<string,StepReadiness>):StepReadiness {
 return step.entryPresent?(checks[step.id]||{state:'unchecked'}):{state:'missing_source',detail:'此步骤暂不可用，请选择其他分析步骤。'};
}

export const readinessLabels:Record<StepReadiness['state'],string>={ready:'软件已检查',missing_environment:'缺少环境',unchecked:'环境未检查',missing_source:'暂不可用'};
