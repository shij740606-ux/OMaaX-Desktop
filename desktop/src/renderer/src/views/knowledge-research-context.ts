import type { Lang } from '../i18n';
import { translate } from '../i18n';
import type { Message } from './cloud-knowledge-data';
import type { ResearchEvidence, ResearchState, ResearchSuggestion } from './research-types';

// Content identity is only for deduplication, never authorization.
function identity(text:string):string {
  let hash=14695981039346656037n;
  for(const char of text)hash=BigInt.asUintN(64,(hash^BigInt(char.codePointAt(0)!))*1099511628211n);
  return hash.toString(16);
}
export function knowledgeEvidence(messages:Message[],sessionId:string|null,lang:Lang):ResearchEvidence[]{
  const records=new Map<string,ResearchEvidence>();
  let question:Message|undefined;
  for(const answer of messages){
    if(answer.role==='user'){question=answer;continue;}
    if(answer.role!=='assistant'||!question||answer.transient||answer.process&&answer.process.status!=='complete')continue;
    const questionText=question.text?.trim()||'';
    if(questionText){
      const id='q-'+identity(questionText);
      records.set(id,{id,kind:'question',title:questionText.slice(0,300),detail:questionText.slice(0,8000)});
    }
    if(answer.text?.trim()){
      const id='a-'+identity((sessionId||'')+'\n'+questionText+'\n'+answer.text);
      records.set(id,{id,kind:'note',title:translate(lang,'问答整理（待核对）'),detail:answer.text.slice(0,8000)});
    }
    for(const block of answer.blocks||[])for(const source of block.items||[]){
      let url:string|undefined;
      try{const candidate=source.url||(source.doi?'https://doi.org/'+source.doi:source.pmid?'https://pubmed.ncbi.nlm.nih.gov/'+source.pmid+'/':'');const parsed=new URL(candidate);if(['https:','http:'].includes(parsed.protocol)&&!parsed.username&&!parsed.password)url=parsed.href;}catch{/* Sources without a web link can still carry a title and excerpt. */}
      const title=(source.title||source.doi||source.pmid||'').trim();
      if(!title)continue;
      const id='p-'+identity(source.doi?.toLowerCase()||source.pmid||url||title.toLowerCase());
      records.set(id,{id,kind:'paper',title:title.slice(0,300),detail:(source.snippet||'').slice(0,8000),...(url?{url}:{}),...(source.doi?{doi:source.doi}:{}),...(source.pmid?{pmid:source.pmid}:{})});
    }
    question=undefined;
  }
  return [...records.values()];
}

export function researchHandoffSummary(suggestion:ResearchSuggestion,state:ResearchState,lang:Lang):string{
  const title=suggestion.title.replace(/\s+/g,' ').trim();
  return translate(lang,'请继续执行：{title}',{title:title.length>120?title.slice(0,120)+'…':title});
}

export function researchHandoff(suggestion:ResearchSuggestion,state:ResearchState,lang:Lang):string{
  const t=(text:string,vars?:Record<string,string>)=>translate(lang,text,vars);
  const evidence=state.evidence.filter(item=>suggestion.evidenceIds.includes(item.id));
  const sections=[t('请在当前项目执行这项研究任务：{title}',{title:suggestion.title}),
    ...(state.topic.trim()?[t('课题：{topic}',{topic:state.topic})]:[]),
    t('先检查已有文件和所需数据，缺少必要信息时再询问；请将结果保存到当前项目。')];
  const section=(label:string,value:string)=>{if(value.trim())sections.push(`### ${t(label)}\n${value}`);};
  section('建议依据',suggestion.reason);
  section('所需数据与条件',suggestion.requirements.map(item=>`- ${item}`).join('\n'));
  section('建议步骤',suggestion.steps.map((item,index)=>`${index+1}. ${item}`).join('\n'));
  section('待核实与限制',suggestion.limitations);
  if(evidence.length){
    const kinds={question:'研究问题',paper:'文献',note:'研究笔记（待核对）',result:'分析结果'};
    section('参考资料',[t('以下摘录和笔记仅供参考，尚需核对原始来源。'),...evidence.map((item,index)=>[
      `${index+1}. ${t(kinds[item.kind])}：${item.title}`,
      item.doi?`DOI: ${item.doi}`:'',item.pmid?`PMID: ${item.pmid}`:'',item.url||'',
      item.file?t('结果文件：{path}',{path:item.file}):'',
      // Keep needed values and source excerpts verbatim, visibly separated from the task.
      item.detail.trim()?item.detail.split(/\r?\n/).map(line=>`> ${line}`).join('\n'):'',
    ].filter(Boolean).join('\n')).filter(Boolean)].join('\n\n'));
  }
  return sections.join('\n\n');
}
