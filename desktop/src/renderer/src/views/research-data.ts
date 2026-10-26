import type {ResearchEvidence, ResearchState} from './research-types';

export const MAX_RESEARCH_EVIDENCE = 80;
export function emptyResearchState(): ResearchState {
 return {version:1,revision:0,topic:'',evidence:[],analysis:null,analysisStale:false,updatedAt:''};
}
export function researchSourceUrl(evidence: ResearchEvidence): string | null {
 const value=evidence.url||(evidence.doi?'https://doi.org/'+evidence.doi:evidence.pmid?'https://pubmed.ncbi.nlm.nih.gov/'+evidence.pmid+'/':'');
 try {const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}
}
function evidenceKey(evidence:ResearchEvidence): string {
 const doi=evidence.doi?.trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i,'').toLowerCase();
 if(doi)return 'doi:'+doi;
 if(evidence.pmid?.trim())return 'pmid:'+evidence.pmid.trim();
 const url=researchSourceUrl(evidence);
 if(url){const normalized=new URL(url);normalized.hash='';return 'url:'+normalized.href;}
 return [evidence.kind,evidence.title.trim().toLowerCase(),evidence.detail.trim()].join('\n');
}
export function mergeResearchEvidence(existing:ResearchEvidence[],incoming:ResearchEvidence[]) {
 const evidence=[...existing],keys=new Set(existing.map(evidenceKey)),ids=new Set(existing.map(item=>item.id));
 let added=0,duplicates=0,omitted=0;
 for(const raw of incoming){
  const item={...raw,title:raw.title.slice(0,300),detail:raw.detail.slice(0,8000)};
  const key=evidenceKey(item);
  if(keys.has(key)){duplicates++;continue;}
  if(evidence.length>=MAX_RESEARCH_EVIDENCE){omitted++;continue;}
  let id=item.id||crypto.randomUUID();while(ids.has(id))id=crypto.randomUUID();
  evidence.push({...item,id});
  ids.add(id);keys.add(key);added++;
 }
 return {evidence,added,duplicates,omitted};
}
export function researchFailure(error:unknown): string {
 const message=String(error);
 if(/RESEARCH_CANCELLED/.test(message))return '已取消更新，原有内容已保留';
 if(/RESEARCH_CONFLICT/.test(message))return '其他窗口已更新此项目。本地修改已保留，请复制需要保留的内容，再重新加载。';
 if(/RESEARCH_CORRUPT/.test(message))return '已保存的研究记录无法读取，原记录已保留，请联系支持人员。';
 if(/RESEARCH_ACCOUNT_CHANGED/.test(message))return '账号已切换，请在当前账号下重新打开项目。';
 if(/RESEARCH_PROJECT/.test(message))return '当前项目暂时无法访问，请确认项目文件夹仍然存在。';
 if(/RESEARCH_CONNECTION/.test(message))return '模型连接未建立，请检查网络和登录状态后重试。';
 if(/RESEARCH_IDLE_TIMEOUT/.test(message))return '模型服务长时间未返回新内容，原有内容已保留，请重试。';
 if(/RESEARCH_INTERRUPTED/.test(message))return '研究内容接收中断，原有内容已保留，请重试。';
 if(/RESEARCH_MODEL_INVALID/.test(message))return '模型回复格式异常，原有内容已保留，请重试。';
 if(/RESEARCH_TRUNCATED/.test(message))return '模型回复未完整生成，原有内容已保留，请重试。';
 if(/RESEARCH_LOGIN/.test(message))return '登录已失效，请重新登录后更新研究内容。';
 if(/RESEARCH_PERMISSION/.test(message))return '当前账号暂不能使用模型服务，请联系管理员。';
 if(/RESEARCH_CONTEXT/.test(message))return '研究资料超出模型可接收范围，请精简资料后重试。';
 if(/RESEARCH_MODEL|RESEARCH_SERVICE/.test(message))return '模型服务暂时不可用，请检查登录状态和模型权限后重试。';
 if(/RESEARCH_STORAGE/.test(message))return '研究内容暂时无法保存，请检查可用空间后重试。本地修改已保留。';
 if(/RESEARCH_BUSY/.test(message))return '当前研究内容正在更新，请稍后重试。';
 if(/UNAUTHORIZED|FORBIDDEN/.test(message))return '请重新登录，并确认当前账号已开通模型权限。';
 if(/RESEARCH_LIMIT/.test(message))return '研究资料过多，请减少资料数量或缩短笔记后重试。';
 if(/RESEARCH_INVALID/.test(message))return '研究内容格式不符合要求，请检查资料或重新生成。';
 return '操作未完成，本地内容已保留，请稍后重试。';
}
