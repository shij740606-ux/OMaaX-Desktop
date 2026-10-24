import {useT} from '../i18n';
import {useEffect,useState} from 'react';
import {optionLabel,parameterCopy,type ParameterSchema} from './workflowParameterHelp';

const textValue=(v:unknown)=>v===null||v===undefined?'':typeof v==='string'?v:JSON.stringify(v);

export default function WorkflowParameterField({name,stepId,value,schema={},required=false,path=name,onChange,onValidationChange}:{name:string;stepId:string;value:unknown;schema?:ParameterSchema;required?:boolean;path?:string;onChange:(v:unknown)=>void;onValidationChange?:(path:string,message:string)=>void}){
 const {t,lang}=useT();
 const copy=parameterCopy(name,stepId,schema,value,required);
 const [listDraft,setListDraft]=useState<string|null>(null),[listError,setListError]=useState('');
 useEffect(()=>()=>onValidationChange?.(path,''),[]);
 const validate=(message:string)=>{setListError(message);onValidationChange?.(path,message);};
 const heading=<span className="workflow-param-heading"><strong>{t(copy.name || '')}</strong><span className={'workflow-param-necessity '+(required?'is-required':'')}>{t(copy.necessity || '')}</span></span>;
 if(value&&typeof value==='object'&&!Array.isArray(value))return <fieldset className="workflow-param-group"><legend>{t(copy.name || '')}</legend><div className="workflow-param-grid">{Object.entries(value as Record<string,unknown>).map(([k,v])=><WorkflowParameterField key={k} name={k} stepId={stepId} path={path+'.'+k} value={v} schema={schema.properties?.[k]} required={Array.isArray(schema.required)&&schema.required.includes(k)} onValidationChange={onValidationChange} onChange={next=>onChange({...value,[k]:next})}/>)}</div></fieldset>;
 const choices=schema.enum;
 const numeric=schema.type==='integer'||schema.type==='number'||typeof value==='number';
 let field;
 if((typeof value==='boolean'||schema.type==='boolean')&&!choices)field=<label className="workflow-param-toggle"><input type="checkbox" aria-label={t(copy.name || '')} checked={value===true} onChange={e=>onChange(e.target.checked)}/>{value===true?t("已启用"):t("未启用")}</label>;
 else if(Array.isArray(value)||schema.type==='array'){
  const objects=Array.isArray(value)&&value.some(v=>v&&typeof v==='object')||schema.items?.type==='object';
  const display=objects?JSON.stringify(value??[],null,2):Array.isArray(value)?value.join(', '):textValue(value);
  const edit=(text:string)=>{
   setListDraft(text);validate('');
   if(objects){try{const parsed=JSON.parse(text);if(!Array.isArray(parsed))throw Error();onChange(parsed);}catch{validate('列表格式尚未完整，完成后才能保存这个修改。');}return;}
   try{const parsed=JSON.parse(text);if(Array.isArray(parsed)){onChange(parsed);return;}}catch{}
   const parts=text.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
   const numbers=schema.items?.type==='integer'||schema.items?.type==='number'||Array.isArray(value)&&value.some(v=>typeof v==='number');
   if(numbers&&parts.some(v=>!Number.isFinite(Number(v)))){validate('请填写数值，多个值用逗号分隔。');return;}
   onChange(numbers?parts.map(Number):parts);
  };
  field=<>{objects?<textarea className="field" aria-label={t(copy.name || '')} value={listDraft??display} rows={5} onChange={e=>edit(e.target.value)}/>:<input className="field" aria-label={t(copy.name || '')} value={listDraft??display} onChange={e=>edit(e.target.value)}/>}<small>{objects?t("按照原有列表结构填写；没有需要时保持原样。"):t("多个值用逗号分隔。")}</small>{listError&&<small role="alert" className="workflow-param-error">{t(listError || '')}</small>}</>;
 }else if(choices)field=<select className="field" aria-label={t(copy.name || '')} value={textValue(value)} onChange={e=>onChange(choices.find(v=>textValue(v)===e.target.value))}>{!choices.some(v=>textValue(v)===textValue(value))&&<option value="">{t("请选择")}</option>}{choices.map(v=><option key={textValue(v)} value={textValue(v)}>{t(optionLabel(v,name) || '')}</option>)}</select>;
 else field=<input className="field" aria-label={t(copy.name || '')} value={textValue(value)} type={numeric?'number':'text'} step={schema.type==='integer'?'1':'any'} required={required} placeholder={required?t("请填写"):copy.empty?t("可留空，按需要填写"):undefined} onChange={e=>onChange(e.target.value===''?null:numeric?Number(e.target.value):e.target.value)}/>;
 return <div className="workflow-param-field">{heading}{field}<p className="workflow-param-hint">{t(copy.hint || '')}</p>{schema.default!==undefined&&schema.default!==null&&typeof schema.default!=='object'&&<small className="workflow-param-default">{t("默认设置：")}{typeof schema.default==='boolean'?(schema.default?t("启用"):t("关闭")):choices?t(optionLabel(schema.default,name) || ''):textValue(schema.default)||t("留空")}</small>}</div>;
}
