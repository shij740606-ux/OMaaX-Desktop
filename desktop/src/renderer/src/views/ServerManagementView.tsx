import { useEffect, useState } from 'react';
import HostsView from './HostsView';
import PersonalComputeView from './PersonalComputeView';

const sections = [['connections','SSH 连接'],['resources','资源监控'],['tasks','任务与调度'],['data','跨服务器数据'],['protection','数据保护与审计']] as const;
type Section = typeof sections[number][0];
export default function ServerManagementView({initial='connections'}:{initial?:Section}) {
  const [section,setSection]=useState<Section>(()=>sessionStorage.getItem('omaax.files.open')?'data':initial);
  useEffect(()=>{if(!sessionStorage.getItem('omaax.files.open'))setSection(initial);},[initial]);
  return <div className="h-full flex flex-col"><header className="drag h-[52px] shrink-0 flex items-center pl-5 hairline-b"><span className="t-title2">服务器管理</span></header>
    <nav aria-label="服务器管理栏目" className="no-drag flex flex-wrap gap-2 px-5 py-3 hairline-b">{sections.map(([id,label])=><button key={id} className={`btn ${section===id?'btn-primary':'btn-ghost'}`} aria-pressed={section===id} onClick={()=>setSection(id)}>{label}</button>)}</nav>
    <div className="flex-1 min-h-0 overflow-y-auto">{section==='connections'?<HostsView/>:<PersonalComputeView key={section} section={section}/>}</div>
  </div>;
}
