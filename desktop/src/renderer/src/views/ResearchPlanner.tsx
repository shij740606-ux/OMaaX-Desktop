import {Component,useEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import {Background,BaseEdge,Controls,MarkerType,Position,ReactFlow,type Edge,type EdgeProps,type Node,type ReactFlowInstance} from '@xyflow/react';
import {BookOpen,Check,FileText,Lightbulb,Network,Plus,RefreshCw,Save,Send,Square,Trash2} from 'lucide-react';
import '@xyflow/react/dist/style.css';
import './ResearchPlanner.css';
import {useT} from '../i18n';
import {emptyResearchState,MAX_RESEARCH_EVIDENCE,mergeResearchEvidence,researchFailure,researchSourceUrl} from './research-data';
import {layoutResearchGraph,RESEARCH_NODE_HEIGHT,RESEARCH_NODE_WIDTH,type ResearchGraphLayout} from './research-graph-layout';
import type {ResearchAnalysis,ResearchEvidence,ResearchProject,ResearchState,ResearchSuggestion,ResearchTab} from './research-types';

type Props={project:ResearchProject;incoming:ResearchEvidence[];onHandoff:(suggestion:ResearchSuggestion,state:ResearchState)=>Promise<void>;onDirtyChange?:(dirty:boolean)=>void};
type Selection={kind:'node'|'edge';id:string}|null;
const nodeLabels={question:'研究问题',paper:'文献',method:'研究方法',finding:'研究发现'};
const evidenceLabels={question:'研究问题',paper:'文献',note:'研究笔记（待核对）',result:'分析结果'};
const request=async<T,>(action:string,body:unknown)=>await window.biodsh.research(action,body) as T;
type RoutedEdge=Edge<{path:string;title:string}>;
function ResearchRelationEdge({id,data,style,markerEnd}:EdgeProps<RoutedEdge>){
 return <g><title>{data?.title}</title><BaseEdge id={id} path={data?.path||''} style={style} markerEnd={markerEnd} interactionWidth={20}/></g>;
}
const edgeTypes={researchRelation:ResearchRelationEdge};
const graphFitOptions={padding:0.18,minZoom:0.03,maxZoom:1};
class ResearchGraphBoundary extends Component<{children:ReactNode;fallback:ReactNode},{failed:boolean}>{
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true};}
 render(){return this.state.failed?this.props.fallback:this.props.children;}
}
type ResearchProgress={phase:'connecting'|'generating'|'validating';receivedChars:number};
const progressLabels={connecting:'正在连接模型服务…',generating:'正在接收研究内容…',validating:'正在校验研究内容…'};

export default function ResearchPlanner({project,incoming,onHandoff,onDirtyChange}:Props){
 const {t,lang}=useT();
 const [tab,setTab]=useState<ResearchTab>('graph');
 const [state,setState]=useState<ResearchState|null>(null),[dirty,setDirty]=useState(false),[loading,setLoading]=useState(true);
 const [saving,setSaving]=useState(false),[generating,setGenerating]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [progress,setProgress]=useState<ResearchProgress>({phase:'connecting',receivedChars:0}),[elapsed,setElapsed]=useState(0);
 const [noteTitle,setNoteTitle]=useState(''),[noteDetail,setNoteDetail]=useState(''),[selected,setSelected]=useState<Selection>(null);
 const [handoffId,setHandoffId]=useState<string|null>(null),[sent,setSent]=useState<string[]>([]);
 const epoch=useRef(0),requestId=useRef<string|null>(null),locked=useRef(false),handoffLock=useRef(false),reload=useRef(0);
 const [loadKey,setLoadKey]=useState(0);
 const [layoutResult,setLayoutResult]=useState<{analysis:ResearchAnalysis;value:ResearchGraphLayout}|null>(null),[layoutError,setLayoutError]=useState(false),[layoutRetry,setLayoutRetry]=useState(0),[hoveredEdge,setHoveredEdge]=useState<string|null>(null);
 // An analysis can change before effects run; never render its nodes with the previous layout.
 const layout=layoutResult?.analysis===state?.analysis?layoutResult?.value:null;
 const graphContainer=useRef<HTMLDivElement>(null),graphView=useRef<ReactFlowInstance<Node,RoutedEdge>|null>(null);
 const selectionRef=useRef(selected);selectionRef.current=selected;
 function showReadableMap(){
  const nodes=state?.analysis?.nodes||[],selection=selectionRef.current;
  const node=(selection?.kind==='node'?nodes.find(item=>item.id===selection.id):undefined)||nodes.find(item=>item.kind==='question')||nodes[0];
  const position=node&&layout?.positions[node.id];
  if(position)void graphView.current?.setCenter(position.x+RESEARCH_NODE_WIDTH/2,position.y+RESEARCH_NODE_HEIGHT/2,{zoom:1});
 }
 useEffect(()=>{
  const controller=new AbortController(),analysis=state?.analysis;setLayoutResult(null);setLayoutError(false);setHoveredEdge(null);
  if(analysis)void layoutResearchGraph(analysis,controller.signal).then(value=>{if(!controller.signal.aborted)setLayoutResult({analysis,value});}).catch(()=>{if(!controller.signal.aborted)setLayoutError(true);});
  return()=>{controller.abort();};
 },[state?.analysis,layoutRetry]);
 useEffect(()=>window.biodsh.onEvent?.(event=>{
  if(event.type!=='research-progress'||event.requestId!==requestId.current)return;
  setProgress(previous=>({phase:event.phase,receivedChars:Math.max(previous.receivedChars,event.receivedChars)}));
 }),[]);
 useEffect(()=>{
  if(!generating)return;
  const started=Date.now();setElapsed(0);
  const timer=setInterval(()=>setElapsed(Math.floor((Date.now()-started)/1000)),1000);
  return()=>clearInterval(timer);
 },[generating]);
 useEffect(()=>{
  const element=graphContainer.current;if(!element||tab!=='graph')return;
  let frame=0;
  const observer=new ResizeObserver(entries=>{
   const {width,height}=entries[0].contentRect;if(!width||!height)return;
   cancelAnimationFrame(frame);frame=requestAnimationFrame(showReadableMap);
  });
  observer.observe(element);
  return()=>{observer.disconnect();cancelAnimationFrame(frame);};
 },[tab,layout,loading]);
 const changed=dirty||!!noteTitle||!!noteDetail;
 useEffect(()=>{onDirtyChange?.(changed);},[changed,onDirtyChange]);
 useEffect(()=>{
  const current=++epoch.current;locked.current=true;handoffLock.current=false;
  setState(null);setDirty(false);setLoading(true);setSaving(false);setGenerating(false);setError('');setNotice('');setSelected(null);setNoteTitle('');setNoteDetail('');setSent([]);setHandoffId(null);
  void request<ResearchState|null>('load',{project:project.path}).then(value=>{if(current===epoch.current)setState(value||emptyResearchState());}).catch(cause=>{if(current===epoch.current)setError(researchFailure(cause));}).finally(()=>{if(current===epoch.current){locked.current=false;setLoading(false);}});
  return()=>{epoch.current++;const active=requestId.current;requestId.current=null;if(active)void request('cancel',{requestId:active}).catch(()=>{});};
 },[project.path,project.workspaceId,loadKey]);
 const busy=loading||saving||generating;
 function edit(next:ResearchState){setState(next);setDirty(true);setNotice('');setError('');}
 function collect(){
  if(!state||locked.current)return;
  const merged=mergeResearchEvidence(state.evidence,incoming);
  if(merged.added)edit({...state,evidence:merged.evidence,analysisStale:!!state.analysis});
  setNotice(merged.omitted?'最多收录 80 条资料，请移除不需要的资料后继续。':merged.added?'已收录当前问答资料，请保存研究内容。':'这些资料已收录，无需重复添加。');
 }
 function addNote(){
  if(!state||locked.current||!noteDetail.trim())return;
  if(state.evidence.length>=MAX_RESEARCH_EVIDENCE){setError('最多收录 80 条资料，请移除不需要的资料后继续。');return;}
  const note:ResearchEvidence={id:crypto.randomUUID(),kind:'note',title:noteTitle.trim()||noteDetail.trim().slice(0,60),detail:noteDetail.trim()};
  const merged=mergeResearchEvidence(state.evidence,[note]);
  if(merged.added){edit({...state,evidence:merged.evidence,analysisStale:!!state.analysis});setNoteTitle('');setNoteDetail('');}
  else setNotice('这些资料已收录，无需重复添加。');
 }
 function removeEvidence(id:string){
  if(!state||locked.current)return;
  if(state.analysis&&!window.confirm(t('移除资料后，需要重新生成图谱和建议。确定移除吗？')))return;
  edit({...state,evidence:state.evidence.filter(item=>item.id!==id),analysis:null,analysisStale:true});setSelected(null);setSent([]);
 }
 async function save(){
  if(!state||locked.current)return;
  locked.current=true;setSaving(true);setError('');const current=epoch.current;
  try{const saved=await request<ResearchState>('save',{project:project.path,expectedRevision:state.revision,state});if(current===epoch.current){setState(saved);setDirty(false);setNotice('研究内容已保存到当前项目。');}}
  catch(cause){if(current===epoch.current)setError(researchFailure(cause));}
  finally{if(current===epoch.current){locked.current=false;setSaving(false);}}
 }
 async function generate(){
  if(!state||locked.current||!state.topic.trim())return;
  locked.current=true;setProgress({phase:'connecting',receivedChars:0});setGenerating(true);setError('');setNotice('');
  const current=epoch.current,id=crypto.randomUUID(),snapshot=state;let publishing=false;requestId.current=id;
  const valid=()=>current===epoch.current&&requestId.current===id;
  try{
   const analysis=await request<ResearchAnalysis>('generate',{project:project.path,requestId:id,topic:state.topic.trim(),evidence:state.evidence,lang});
   if(!valid())return;
   const next={...snapshot,analysis,analysisStale:false};setState(next);setDirty(true);setSelected(null);setSent([]);
   publishing=true;requestId.current=null;setGenerating(false);setSaving(true);
   const saved=await request<ResearchState>('save',{project:project.path,expectedRevision:snapshot.revision,state:next});
   if(current===epoch.current){setState(saved);setDirty(false);setNotice('图谱和建议已更新，并保存到当前项目。');}
  }catch(cause){if(current===epoch.current&&(publishing||requestId.current===id))setError(researchFailure(cause));}
  finally{if(current===epoch.current&&(publishing||requestId.current===id)){requestId.current=null;locked.current=false;setGenerating(false);setSaving(false);}}
 }
 function cancel(){
  const id=requestId.current;if(!id)return;requestId.current=null;locked.current=false;setGenerating(false);setNotice('已取消更新，原有内容已保留');
  void request('cancel',{requestId:id}).catch(()=>{});
 }
 async function handoff(suggestion:ResearchSuggestion){
  if(!state||handoffLock.current||busy||sent.includes(suggestion.id))return;
  handoffLock.current=true;setHandoffId(suggestion.id);setError('');const current=epoch.current;
  try{await onHandoff(suggestion,state);if(current===epoch.current){setSent(previous=>[...previous,suggestion.id]);setNotice('已交给主对话，可继续补充要求。');}}
  catch(cause){if(current===epoch.current)setError(cause instanceof Error&&cause.message==='RESEARCH_HANDOFF_NOT_READY'?'请重启智能体后再交给主对话。':'未能交给主对话，研究建议已保留，请重试。');}
  finally{if(current===epoch.current){handoffLock.current=false;setHandoffId(null);}}
 }
 function reloadSaved(){
  if(changed&&!window.confirm(t('重新加载会放弃尚未保存的修改，确定继续吗？')))return;
  setLoadKey(++reload.current);
 }
 const graph=useMemo(()=>{
  if(!layout)return {nodes:[],edges:[]};
  const nodes:Node[]=(state?.analysis?.nodes||[]).map(node=>({id:node.id,position:layout.positions[node.id],sourcePosition:Position.Right,targetPosition:Position.Left,style:{width:RESEARCH_NODE_WIDTH,height:RESEARCH_NODE_HEIGHT},ariaLabel:node.label,data:{label:<div className="research-node-label"><span>{t(nodeLabels[node.kind])}</span><strong title={node.label}>{node.label}</strong><small>{t(node.basis==='source'?'有资料引用':'待验证假设')}</small></div>},selected:selected?.kind==='node'&&selected.id===node.id,className:'research-node research-node-'+node.basis}));
  const activeEdge=hoveredEdge||(selected?.kind==='edge'?selected.id:null);
  const edges:RoutedEdge[]=(state?.analysis?.edges||[]).map(edge=>{
   const highlighted=activeEdge===edge.id,related=selected?.kind==='node'&&(edge.source===selected.id||edge.target===selected.id);
   const color=highlighted||related?'var(--accent)':edge.basis==='hypothesis'?'#a48bb6':'#6999ae';
   return {id:edge.id,source:edge.source,target:edge.target,type:'researchRelation',data:{path:layout.paths[edge.id],title:edge.label},ariaLabel:edge.label,style:{stroke:color,strokeWidth:highlighted||related?2.4:1.3,opacity:activeEdge&&!highlighted?0.35:1,strokeDasharray:edge.basis==='hypothesis'?'5 4':undefined},markerEnd:{type:MarkerType.ArrowClosed,color},selected:selected?.kind==='edge'&&selected.id===edge.id,zIndex:highlighted?2:0};
  });
  return {nodes,edges};
 },[state?.analysis,layout,selected,hoveredEdge,t]);
 const graphLabels={'controls.ariaLabel':t('图谱缩放'),'controls.zoomIn.ariaLabel':t('放大'),'controls.zoomOut.ariaLabel':t('缩小'),'controls.fitView.ariaLabel':t('显示完整图谱'),'node.a11yDescription.default':t('按回车选择节点，按退出键取消选择。'),'edge.a11yDescription.default':t('按回车选择连线，按退出键取消选择。')};
 const item=selected?.kind==='node'?state?.analysis?.nodes.find(node=>node.id===selected.id):selected?.kind==='edge'?state?.analysis?.edges.find(edge=>edge.id===selected.id):null;
 const layoutFailure=<div className="research-empty" role="alert"><p>{t('图谱布局未能完成，研究内容已保留。')}</p><button type="button" className="btn btn-ghost" onClick={()=>setLayoutRetry(value=>value+1)}>{t('重新布局')}</button></div>;
 function sourceList(ids:string[]){
  const items=state?.evidence.filter(evidence=>ids.includes(evidence.id))||[];
  return items.length?<ul className="research-references">{items.map(evidence=><li key={evidence.id}><strong>{evidence.title}</strong><small>{t(evidenceLabels[evidence.kind])}</small>{evidence.detail&&<p>{evidence.detail}</p>}{evidence.doi&&<small>DOI: {evidence.doi}</small>}{evidence.pmid&&<small>PMID: {evidence.pmid}</small>}{evidence.file&&<small>{evidence.file}</small>}{researchSourceUrl(evidence)&&<button type="button" className="btn btn-ghost" onClick={()=>void window.biodsh.openExternal(researchSourceUrl(evidence)!).catch(()=>setError('链接无法打开，请稍后重试'))}>{t('查看来源')}</button>}</li>)}</ul>:<p className="research-muted">{t('尚无资料引用，需要进一步核实。')}</p>;
 }
 return <section className="research-planner" aria-label={t('课题研究')}>
  <div className="research-workspace">
   <div className="research-toolbar"><div><h2>{t('课题研究')}</h2><p>{t('一份课题资料，理清已有关系，找到下一步方向。')}</p></div><div className="research-actions"><button type="button" className="btn btn-ghost" disabled={busy} onClick={reloadSaved}>{t('重新加载')}</button><button type="button" className="btn btn-ghost" disabled={busy||!state||!dirty} onClick={()=>void save()}><Save size={15}/>{t(saving?'正在保存…':'保存研究内容')}</button>{generating?<button type="button" className="btn btn-ghost" onClick={cancel}><Square size={14}/>{t('取消更新')}</button>:<button type="button" className="btn btn-primary" disabled={busy||!state?.topic.trim()} onClick={()=>void generate()}><RefreshCw size={15}/>{t(state?.analysis?'更新图谱与建议':'生成图谱与建议')}</button>}</div></div>
   {error&&<p role="alert" className="research-error">{t(error)}</p>}
   {notice&&<p role="status" className="research-notice">{t(notice)}</p>}
   {loading?<div className="research-empty" role="status">{t('正在读取当前项目的研究内容…')}</div>:!state?<div className="research-empty"><p>{t('研究内容未能加载，请重试。')}</p><button type="button" className="btn btn-primary" onClick={reloadSaved}>{t('重试')}</button></div>:<>
    <div className="research-topic-card"><label htmlFor="research-topic">{t('课题名称')}</label><input id="research-topic" value={state.topic} maxLength={2000} disabled={busy} placeholder={t('例如：水稻耐盐研究中的候选基因与验证路径')} onChange={event=>edit({...state,topic:event.target.value,analysisStale:!!state.analysis})}/><div className="research-meta"><span>{t('已收录 {n} 条资料',{n:state.evidence.length})}</span><span role="status">{t(changed?'有尚未保存的修改':'研究内容已保存')}{!changed&&state.updatedAt?' · '+new Date(state.updatedAt).toLocaleString(lang==='en'?'en-US':'zh-CN'):''}</span></div></div>
    <details className="research-materials"><summary><BookOpen size={16}/><strong>{t('研究资料')}</strong><span className="research-materials-count">{state.evidence.length} / {MAX_RESEARCH_EVIDENCE}</span><span className="research-materials-hint">{t('展开管理文献与笔记')}</span></summary><div className="research-materials-body"><div className="research-materials-heading"><p>{t('仅收录你选定的问答资料和笔记。模型回答与研究笔记需要核对，不作为已证实结论。')}</p><button type="button" className="btn btn-ghost" disabled={busy||!incoming.length||state.evidence.length>=MAX_RESEARCH_EVIDENCE} onClick={collect}><Plus size={15}/>{t('收录当前问答结果')}</button></div>
     {state.evidence.length>0&&<ul className="research-evidence-list">{state.evidence.map(evidence=><li key={evidence.id}><details><summary><span>{t(evidenceLabels[evidence.kind])}</span><strong>{evidence.title}</strong></summary><p>{evidence.detail}</p>{evidence.doi&&<small>DOI: {evidence.doi}</small>}{researchSourceUrl(evidence)&&<button type="button" className="btn btn-ghost" onClick={()=>void window.biodsh.openExternal(researchSourceUrl(evidence)!).catch(()=>setError('链接无法打开，请稍后重试'))}>{t('查看来源')}</button>}</details><button type="button" className="btn btn-ghost research-remove" disabled={busy} aria-label={t('移除资料：{title}',{title:evidence.title})} onClick={()=>removeEvidence(evidence.id)}><Trash2 size={15}/></button></li>)}</ul>}
     <div className="research-note"><label htmlFor="research-note-title">{t('研究笔记')}</label><input id="research-note-title" value={noteTitle} maxLength={300} disabled={busy} placeholder={t('笔记标题（选填）')} onChange={event=>setNoteTitle(event.target.value)}/><textarea aria-label={t('笔记内容')} value={noteDetail} maxLength={8000} rows={3} disabled={busy} placeholder={t('记录研究问题、观察结果或待验证的想法…')} onChange={event=>setNoteDetail(event.target.value)}/><div><small>{noteDetail.length} / 8000</small><button type="button" className="btn btn-ghost" disabled={busy||!noteDetail.trim()} onClick={addNote}><Plus size={15}/>{t('添加笔记')}</button></div>{(noteTitle||noteDetail)&&<p className="research-muted">{t('点击“添加笔记”后，再保存研究内容。')}</p>}</div>
    </div></details>
    {generating&&<p className="research-notice research-progress" role="status"><strong>{t(progressLabels[progress.phase])}</strong><span>{t('已等待 {seconds} 秒',{seconds:elapsed})}{progress.receivedChars>0?' · '+t('已接收 {count} 字符',{count:progress.receivedChars.toLocaleString(lang==='en'?'en-US':'zh-CN')}):''}</span></p>}
    {state.analysisStale&&state.analysis&&<p className="research-stale">{t('资料或课题已变更，下方仍为上次结果。点击“更新图谱与建议”生成新版本。')}</p>}
    <div className="research-view-tabs" role="tablist" aria-label={t('研究结果视图')}>
     {([{id:'graph',label:'关系图谱',description:'看清问题、文献与研究发现之间的联系',Icon:Network},{id:'suggestions',label:'下一步建议',description:'明确所需数据、验证步骤与执行方向',Icon:Lightbulb}] as const).map(({id,label,description,Icon})=><button key={id} type="button" role="tab" aria-label={t(label)} id={'research-tab-'+id} aria-selected={tab===id} aria-controls="research-results" tabIndex={tab===id?0:-1} className={'research-view-tab'+(tab===id?' is-active':'')} onClick={()=>setTab(id)} onKeyDown={event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?'graph':event.key==='End'?'suggestions':tab==='graph'?'suggestions':'graph';setTab(next);document.getElementById('research-tab-'+next)?.focus();}}}><Icon size={21}/><span><strong>{t(label)}</strong><small>{t(description)}</small></span>{state.analysis&&<span className="research-view-count">{id==='graph'?state.analysis.nodes.length:state.analysis.suggestions.length}</span>}</button>)}
    </div>
    <div id="research-results" role="tabpanel" aria-labelledby={'research-tab-'+tab} tabIndex={0}>
    {!state.analysis?<div className="research-empty research-start"><div className="research-start-icon">{tab==='graph'?<Network size={30}/>:<Lightbulb size={30}/>}</div><h3>{t(tab==='graph'?'让研究线索连成图谱':'把研究问题变成下一步行动')}</h3><p>{t(tab==='graph'?'连接研究问题、文献、方法与发现，点击节点可追溯资料依据。':'根据同一份资料整理研究方向、所需数据和验证步骤，可交给主对话继续执行。')}</p><div className="research-start-steps"><span><b>1</b>{t('填写课题')}</span><span><b>2</b>{t('补充资料（选填）')}</span><span><b>3</b>{t('生成图谱与建议')}</span></div><p className="research-muted">{t('打开页面和切换标签不会调用模型。')}</p></div>:<>
     <div className="research-analysis-summary"><h3>{state.analysis.title}</h3><p>{state.analysis.summary}</p></div>
     {tab==='graph'?<div className="research-graph-layout">
      <div className="research-graph-card">
       <div className="research-graph-heading"><span>{t('实线表示资料关联，虚线表示待验证假设。')}</span><span>{t('关系文字在选中后显示，可用下拉框定位节点或关系。')}</span><span>{t('拖动画布查看更多；用“显示完整图谱”查看全局。')}</span>
        <div className="research-selectors"><label>{t('查看节点')}<select aria-label={t('查看节点')} value={selected?.kind==='node'?selected.id:''} onChange={event=>{const id=event.target.value;setSelected(id?{kind:'node',id}:null);if(id)void graphView.current?.fitView({nodes:[{id}],padding:0.2,minZoom:0.6,maxZoom:1,duration:200});}}><option value="">{t('选择节点')}</option>{state.analysis.nodes.map(node=><option key={node.id} value={node.id}>{node.label}</option>)}</select></label>
        <label>{t('查看关系')}<select aria-label={t('查看关系')} value={selected?.kind==='edge'?selected.id:''} onChange={event=>setSelected(event.target.value?{kind:'edge',id:event.target.value}:null)}><option value="">{t('选择关系')}</option>{state.analysis.edges.map(edge=><option key={edge.id} value={edge.id}>{edge.label||t('未命名关系')} · {state.analysis!.nodes.find(node=>node.id===edge.source)?.label} → {state.analysis!.nodes.find(node=>node.id===edge.target)?.label}</option>)}</select></label></div>
       </div>
       <div ref={graphContainer} className="research-graph" aria-label={t('研究关系图')}>
        {layoutError?layoutFailure:!layout?<div className="research-empty" role="status">{t('正在整理图谱布局…')}</div>:<ResearchGraphBoundary fallback={layoutFailure}><ReactFlow onInit={instance=>{graphView.current=instance;showReadableMap();}} key={state.updatedAt+state.analysis.title} nodes={graph.nodes} edges={graph.edges} edgeTypes={edgeTypes} ariaLabelConfig={graphLabels} deleteKeyCode={null} edgesReconnectable={false} minZoom={0.03} maxZoom={1.6} nodesDraggable={false} nodesConnectable={false} onNodeClick={(_,node)=>setSelected({kind:'node',id:node.id})} onEdgeClick={(_,edge)=>setSelected({kind:'edge',id:edge.id})} onEdgeMouseEnter={(_,edge)=>setHoveredEdge(edge.id)} onEdgeMouseLeave={()=>setHoveredEdge(null)} onPaneClick={()=>setSelected(null)}><Background gap={22}/><Controls showInteractive={false} fitViewOptions={graphFitOptions}/></ReactFlow></ResearchGraphBoundary>}
       </div>
      </div>
      <aside className="research-detail" aria-label={t('图谱详情')}>{item?<><span className={'research-basis '+item.basis}>{t(item.basis==='source'?'有资料引用':'待验证假设')}</span><h3>{item.label||t('未命名关系')}</h3>{'summary' in item?<p>{item.summary}</p>:<p className="research-relation-endpoints"><strong>{state.analysis.nodes.find(node=>node.id===item.source)?.label}</strong><span aria-hidden="true">↓</span><strong>{state.analysis.nodes.find(node=>node.id===item.target)?.label}</strong></p>}<h4>{t('关联资料')}</h4>{sourceList(item.evidenceIds)}</>:<div className="research-empty"><FileText size={24}/><p>{t('点击图中的节点或连线，查看说明和资料来源。')}</p></div>}</aside>
     </div>:<div className="research-suggestions">{state.analysis.suggestions.length?state.analysis.suggestions.map((suggestion,index)=><article className="research-suggestion" key={suggestion.id}><div className="research-suggestion-title"><span>{String(index+1).padStart(2,'0')}</span><h3>{suggestion.title}</h3></div><p>{suggestion.reason}</p><h4>{t('所需数据与条件')}</h4>{suggestion.requirements.length?<ul>{suggestion.requirements.map((requirement,i)=><li key={i}>{requirement}</li>)}</ul>:<p className="research-muted">{t('请在执行前确认所需数据。')}</p>}<h4>{t('建议步骤')}</h4><ol>{suggestion.steps.map((step,i)=><li key={i}>{step}</li>)}</ol>{suggestion.limitations&&<div className="research-limitations"><strong>{t('待核实与限制')}</strong><p>{suggestion.limitations}</p></div>}<details className="research-suggestion-sources"><summary>{t('查看关联资料')}</summary>{sourceList(suggestion.evidenceIds)}</details><button type="button" className="btn btn-primary research-handoff" disabled={busy||handoffId!==null||sent.includes(suggestion.id)||state.analysisStale} onClick={()=>void handoff(suggestion)}>{sent.includes(suggestion.id)?<Check size={15}/>:<Send size={15}/>} {t(sent.includes(suggestion.id)?'已交给主对话':handoffId===suggestion.id?'正在交接…':'交给主对话')}</button></article>):<div className="research-empty">{t('当前没有研究建议，可补充资料后更新。')}</div>}</div>}
    </>}
    </div>
   </>}
  </div>
 </section>;
}
