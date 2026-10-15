type Choice={steps:string[];parameters?:Record<string,unknown>;configs?:Record<string,unknown>};
type Task={state:string;detail:string;request?:Choice};
const canonical=(value:unknown):unknown=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>[key,canonical(item)])):value;
const signature=(choice:Choice)=>JSON.stringify(canonical({steps:[...choice.steps].sort(),parameters:choice.parameters||{},configs:choice.configs||{}}));
export const environmentMatches=(task:Task,choice:Choice)=>!!task.request&&signature(task.request)===signature(choice);
export function currentEnvironment<T extends Task>(task:T,choice:Choice):T|Task{
 if(['complete','failed'].includes(task.state)&&!environmentMatches(task,choice))return {state:'idle',detail:'请检查本次所选步骤的环境；以前配置其他步骤的记录不会影响本次选择。'};
 return task;
}
