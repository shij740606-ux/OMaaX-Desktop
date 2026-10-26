import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {BookOpen,GitBranch,RefreshCw} from 'lucide-react';
import {useApp} from '../store';
import {useT} from '../i18n';
import CloudKnowledgeView,{type KnowledgeSourceHandle,type KnowledgeSourceState} from './CloudKnowledgeView';
import ResearchPlanner from './ResearchPlanner';
import {knowledgeEvidence,researchHandoff,researchHandoffSummary} from './knowledge-research-context';
import type {Message} from './cloud-knowledge-data';
import type {ResearchProject,ResearchState,ResearchSuggestion} from './research-types';
import './KnowledgeHubView.css';

export default function KnowledgeHubView({active=true}:{active?:boolean}){
  const {t,lang}=useT();
  const {dsh,currentSession,setCurrentSession,setTab,settings,info}=useApp();
  const [view,setView]=useState<'qa'|'research'>('qa');
  const [opened,setOpened]=useState(false);
  const [projects,setProjects]=useState<ResearchProject[]>([]);
  const [selectedProject,setSelectedProject]=useState<ResearchProject|null>(null);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false),[sending,setSending]=useState(false);
  const [dirty,setDirty]=useState(false);
  const dirtyRef=useRef(false);dirtyRef.current=dirty;
  const loadVersion=useRef(0),alive=useRef(true),handoffPending=useRef(false);
  const qa=useRef<KnowledgeSourceHandle>(null);
  const [source,setSource]=useState<KnowledgeSourceState>({sessions:[],sessionId:null,busy:false,unsaved:false,loading:false,error:''});
  const onSourceChange=useCallback((next:KnowledgeSourceState)=>setSource(next),[]);
  const pendingSessions=useRef(new Map<string,string>());
  const pendingRequests=useRef(new Map<string,{sessionId:string;summary:string;context:string;requestId:string}>());
  const [context,setContext]=useState<{sessionId:string|null;messages:Message[];busy:boolean}>({sessionId:null,messages:[],busy:false});
  const onContextChange=useCallback((next:typeof context)=>setContext(next),[]);
  const fallback=settings?.workspace||info?.paths.workspace||'';
  const allProjects=useMemo(()=>{
    const items=projects.length?projects:fallback?[{workspaceId:fallback,path:fallback,title:t('当前项目'),sessionIds:[]}]:[];
    return selectedProject&&!items.some(item=>item.path===selectedProject.path)?[selectedProject,...items]:items;
  },[projects,fallback,selectedProject,t]);
  const project=selectedProject||allProjects.find(item=>currentSession&&item.sessionIds.includes(currentSession))||allProjects[0];
  const incoming=useMemo(()=>context.busy?[]:knowledgeEvidence(context.messages,context.sessionId,lang),[context,lang]);
  const refresh=useCallback(async()=>{
    if(dsh.state!=='running')return;
    const revision=++loadVersion.current;setLoading(true);
    try{
      const result=await window.biodsh.dshRpc('workspace.list') as {items:ResearchProject[]};
      if(!alive.current||revision!==loadVersion.current)return;
      const items=result.items.filter(item=>typeof item.path==='string'&&typeof item.workspaceId==='string'&&Array.isArray(item.sessionIds));
      setProjects(items);
      const linked=items.find(item=>currentSession&&item.sessionIds.includes(currentSession));
      // The chat supplies the initial destination; afterwards the user's selection stays independent.
      setSelectedProject(previous=>dirtyRef.current?previous:items.find(item=>item.path===previous?.path)||linked||items[0]||previous);
      setError('');
    }catch{if(alive.current&&revision===loadVersion.current)setError('项目列表暂时无法读取，可刷新重试。');}
    finally{if(alive.current&&revision===loadVersion.current)setLoading(false);}
  },[currentSession,dsh.state,fallback]);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;loadVersion.current++;};},[]);
  useEffect(()=>{if(active)void refresh();},[active,refresh]);
  useEffect(()=>{if(!selectedProject&&project)setSelectedProject(project);},[selectedProject,project]);
  const changeProject=(next:string)=>{
    if(dirty&&!window.confirm(t('当前课题有未保存修改。切换项目将放弃这些修改，是否继续？')))return;
    setDirty(false);dirtyRef.current=false;setSelectedProject(allProjects.find(item=>item.path===next)||null);setError('');
  };
  const handoff=async(suggestion:ResearchSuggestion,state:ResearchState)=>{
    if(!project||dsh.state!=='running')throw Error(t('请等待主对话服务启动后再执行研究建议。'));
    if(handoffPending.current)return;
    handoffPending.current=true;setSending(true);
    try{
      const sessionId=pendingSessions.current.get(project.path)||(currentSession&&project.sessionIds.includes(currentSession)?currentSession:await window.biodsh.dshNewSession(project.workspaceId) as string);
      if(!sessionId)throw Error('Missing session');
      if(!alive.current)return;
      pendingSessions.current.set(project.path,sessionId);
      setCurrentSession(sessionId);
      // Remember the native session immediately, so a failed send can be retried there.
      setProjects(items=>items.map(item=>item.path===project.path?{...item,sessionIds:[...new Set([...item.sessionIds,sessionId])]}:item));
      setSelectedProject({...project,sessionIds:[...new Set([...project.sessionIds,sessionId])]});
      const context=researchHandoff(suggestion,state,lang),summary=researchHandoffSummary(suggestion,state,lang),key=project.path+'\n'+suggestion.id;
      const previous=pendingRequests.current.get(key);
      // Keep the request identity after an uncertain response so retry cannot run the task twice.
      const request=previous?.sessionId===sessionId&&previous.summary===summary&&previous.context===context?previous:{sessionId,summary,context,requestId:crypto.randomUUID()};
      pendingRequests.current.set(key,request);
      const receipt=await window.biodsh.dshRpc('research.handoff',{...request,project:project.path}) as {accepted?:boolean}|null;
      if(receipt?.accepted!==true)throw Error('Prompt not accepted');
      pendingRequests.current.delete(key);
      pendingSessions.current.delete(project.path);
      if(alive.current)setTab('chat');
    }catch(error){if(String(error).includes('RESEARCH_HANDOFF_NOT_READY'))throw Error('RESEARCH_HANDOFF_NOT_READY');throw Error(t('研究建议未能交给主对话，请稍后重试。'));}
    finally{handoffPending.current=false;if(alive.current)setSending(false);}
  };
  return <div className="knowledge-hub">
    <div className="knowledge-hub-bar drag">
      <nav aria-label={t('知识库功能')}>
        {([{id:'qa',label:'文献问答',Icon:BookOpen},{id:'research',label:'课题研究',Icon:GitBranch}] as const).map(({id,label,Icon})=><button key={id} type="button" className={'btn '+(view===id?'btn-primary':'btn-ghost')} aria-pressed={view===id} onClick={()=>{setView(id);if(id!=='qa')setOpened(true);}}><Icon size={16}/>{t(label)}</button>)}
      </nav>
    </div>
    <div className="knowledge-hub-selection" hidden={view!=='research'}>
      <div className="knowledge-hub-selectors">
        <div className="knowledge-hub-source"><label htmlFor="research-source">{t('选择问答来源')}</label><div><select id="research-source" aria-label={t('选择问答来源')} className="field" value={source.sessionId||''} disabled={source.busy||source.unsaved||source.loading||sending} onChange={event=>void qa.current?.selectSession(event.target.value)}><option value="">{t(source.loading?'正在加载问答…':source.sessions.length?'请选择问答':'暂无历史问答')}</option>{source.sessionId&&!source.sessions.some(item=>item.id===source.sessionId)&&<option value={source.sessionId}>{t('当前问答')}</option>}{source.sessions.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select><button type="button" className="btn btn-ghost" aria-label={t('刷新问答来源')} disabled={source.busy||source.loading||sending} onClick={()=>void qa.current?.refreshSessions()}><RefreshCw size={14}/></button></div></div>
        <div className="knowledge-hub-project"><label htmlFor="research-destination">{t('保存到哪个项目')}</label><div><select id="research-destination" aria-label={t('保存到哪个项目')} className="field" value={project?.path||''} disabled={sending||loading} onChange={event=>changeProject(event.target.value)}>{!allProjects.length&&<option value="">{t('请选择项目')}</option>}{allProjects.map(item=><option key={item.path} value={item.path}>{item.title||item.path}</option>)}</select><button type="button" className="btn btn-ghost" aria-label={t('刷新项目')} disabled={sending||loading||dsh.state!=='running'} onClick={()=>void refresh()}><RefreshCw size={14}/></button></div></div>
      </div>
      <p className="knowledge-hub-selection-hint">{t('选择问答后，展开“研究资料”收录结果。切换来源不会替换已收录资料。')}</p>
      {source.unsaved?<p role="status" className="knowledge-hub-selection-status">{t('当前问答尚未保存，请回到文献问答完成保存后再切换来源。')}</p>:source.busy?<p role="status" className="knowledge-hub-selection-status">{t('正在处理问答，请稍候再选择来源。')}</p>:source.sessionId&&<p role="status" className="knowledge-hub-selection-status">{t('所选问答有 {n} 条可收录资料',{n:incoming.length})}</p>}
      {source.error&&<p role="alert" className="knowledge-hub-source-error">{t(source.error)}</p>}
    </div>
    {error&&<p role="alert" className="knowledge-hub-error">{t(error)}</p>}
    <div className="knowledge-hub-content" hidden={view!=='qa'}><CloudKnowledgeView ref={qa} onContextChange={onContextChange} onSourceChange={onSourceChange}/></div>
    <div className="knowledge-hub-content" hidden={view==='qa'}>{opened&&(project?<ResearchPlanner project={project} incoming={incoming} onDirtyChange={setDirty} onHandoff={handoff}/>:<p className="knowledge-hub-empty">{t('请先创建或选择一个项目，再开始课题研究。')}</p>)}</div>
  </div>;
}
