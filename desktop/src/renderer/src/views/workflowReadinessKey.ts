type Values=Record<string,unknown>;
type SoftwareStep={id:string;nextflowSource?:string;configDefaults?:Values;configSchema?:{properties?:Record<string,{default?:unknown}>}};
type SoftwareFlow={id:string;nodes:SoftwareStep[];extraNodes:SoftwareStep[]};

// Keep this selector aligned with workflow_environments.requirements/upstream_selection.
export function workflowReadinessKey(server:string,flow:SoftwareFlow|null,parameters:Record<string,Values>,configs:Record<string,Values>){
 const routes=flow?[...flow.nodes,...flow.extraNodes].filter(s=>s.nextflowSource).map(s=>{
  const given={...s.configDefaults,...configs[s.id]};
  if('skip_emptydrops' in given&&!('skip_cellbender' in given))given.skip_cellbender=given.skip_emptydrops;
  return [s.id,Object.entries(s.configSchema?.properties||{}).filter(([key])=>['aligner','peakcaller','profile','technology','refine_bins_dastool','bbnorm'].includes(key)||/^(run_|skip_|use_|perform_)/.test(key)).sort(([a],[b])=>a.localeCompare(b)).map(([key,schema])=>[key,Object.hasOwn(given,key)?given[key]:schema.default])];
 }):[];
 const doublets=configs.singlecell_analyze?.doublets as {enabled?:boolean}|undefined;
 return JSON.stringify([server,flow?.id,parameters.rnaseq_qc?.qc_tool,parameters.rnaseq_quant?.quant_mode,parameters.rnaseq_quant?.abundance_engine,parameters.annotation?.prefer_bioc_gene2go,parameters.annotation?.skip_go,parameters.annotation?.skip_kegg,configs.singlecell_analyze?.integration,doublets?.enabled,routes]);
}
