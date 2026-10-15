import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source=fs.readFileSync(new URL('../src/renderer/src/views/workflowParameterHelp.ts',import.meta.url),'utf8');
const module={exports:{}};
new Function('exports','module',ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText)(module.exports,module);
const {parameterNames,parameterCopy,optionLabel}=module.exports;
const policyModule={exports:{}};new Function('exports','module',ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/views/workflowUserParameters.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText)(policyModule.exports,policyModule);const {isUserParameter}=policyModule.exports;
const catalog=JSON.parse(fs.readFileSync(new URL('../resources/workflows/catalog.json',import.meta.url)));
const keys=new Set();
const inspect=(key,step,schema={},value,required=false)=>{
 if(!(schema.userVisible??isUserParameter(key)))return;
 keys.add(key);
 assert(parameterNames[key]||/[\u4e00-\u9fff]/.test(schema.label||''),`${step}: missing Chinese name for ${key}`);
 const copy=parameterCopy(key,step,schema,value,required);
 assert(/[\u4e00-\u9fff]/.test(copy.name),`${step}: unreadable name for ${key}`);
 assert(copy.hint&&/[\u4e00-\u9fff]/.test(copy.necessity));
 if(required){assert.equal(copy.necessity,'需要填写');assert(copy.common);}
};
for(const flow of catalog.workflows)for(const step of [...flow.nodes,...flow.extraNodes]){
 for(const arg of step.arguments.filter(a=>!a.managed))inspect(arg.key,step.id,{type:arg.type},arg.default,arg.required);
 const visit=s=>{for(const [key,value] of Object.entries(s?.properties||{})){inspect(key,step.id,value,value.default,s.required?.includes?.(key));visit(value);}};
 visit(step.configSchema);
 const defaults=(v,schema)=>{for(const [key,value] of Object.entries(v||{})){inspect(key,step.id,schema?.properties?.[key]||{},value);if(value&&typeof value==='object'&&!Array.isArray(value))defaults(value,schema?.properties?.[key]);}};
 defaults(step.configDefaults,step.configSchema);
}
assert.equal(parameterCopy('msa_file','msa').name,'多序列比对结果文件');
assert.match(parameterCopy('merge_cut_height','rnaseq_wgcna',{},.25).hint,/模块/);
assert.equal(parameterCopy('max_genes','singlecell_analyze').name,'检出基因数上限');
assert.match(parameterCopy('max_genes','singlecell_analyze').hint,/过滤/);
assert.equal(parameterCopy('max_genes','rnaseq_wgcna').name,'最多用于建网的基因数');
assert.equal(parameterCopy('query','ortholog').name,'查询物种蛋白序列文件');
assert.equal(optionLabel('both','seq_types'),'蛋白与编码序列');
assert.equal(optionLabel('global','semibin_environment'),'通用环境');
assert.equal(optionLabel('family','kaiju_taxon_rank'),'科');
assert.equal(optionLabel('salmon','abundance_engine'),'salmon');
console.log(`Chinese names and adjustment guidance cover ${keys.size} parameter keys across all registered workflows.`);
