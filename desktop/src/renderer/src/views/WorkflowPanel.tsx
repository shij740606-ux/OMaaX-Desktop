import {useT} from '../i18n';
import {Component,lazy,Suspense,useEffect,useState,type ReactNode} from 'react';

const WorkflowsView=lazy(()=>import('./WorkflowsView'));

function WorkflowError({retry}:{retry:()=>void}){
 const {t}=useT();
 return <div className="card m-5 p-5" role="alert"><p>{t('流程页面显示失败，请重新载入。服务器上的分析任务不会受影响。')}</p><button className="btn btn-tint mt-3" onClick={retry}>{t('重新载入流程')}</button></div>;
}

class WorkflowErrorBoundary extends Component<{children:ReactNode},{failed:boolean}> {
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true};}
 render(){
  if(this.state.failed)return <WorkflowError retry={()=>this.setState({failed:false})}/>;
  return this.props.children;
 }
}

export default function WorkflowPanel({active}:{active:boolean}){
 const {t,lang}=useT();
 const [visited,setVisited]=useState(active);
 useEffect(()=>{if(active)setVisited(true);},[active]);
 if(!active&&!visited)return null;
 // Preserve the editor and in-flight work when the user switches to a conversation.
 return <div className="h-full" hidden={!active} style={{display:active?'block':'none'}}><WorkflowErrorBoundary><Suspense fallback={<div className="p-5" role="status">{t("正在加载流程…")}</div>}><WorkflowsView visible={active}/></Suspense></WorkflowErrorBoundary></div>;
}
