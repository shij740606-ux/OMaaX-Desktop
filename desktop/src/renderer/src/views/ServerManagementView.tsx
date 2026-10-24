import {useT} from '../i18n';
import { useEffect, useState } from 'react';
import HostsView from './HostsView';
import PersonalComputeView from './PersonalComputeView';

const sections = [['connections','连接服务器'],['files','服务器文件'],['resources','资源监控'],['environments','环境管理'],['tasks','任务与调度'],['data','跨服务器数据'],['protection','数据保护与审计']] as const;
type Section = typeof sections[number][0];
export default function ServerManagementView({initial='connections'}:{initial?:Section}) {
 const {t,lang}=useT();
  const [section,setSection]=useState<Section>(()=>sessionStorage.getItem('omaax.files.open')?'protection':initial);
  useEffect(()=>{if(!sessionStorage.getItem('omaax.files.open'))setSection(initial);},[initial]);
  return <div className="h-full flex flex-col"><header className="drag h-[52px] shrink-0 flex items-center pl-5 hairline-b"><span className="t-title2">{t("服务器管理")}</span></header>
    <nav aria-label={t("服务器管理栏目")} className="no-drag flex flex-wrap gap-2 px-5 py-3 hairline-b">{sections.map(([id,label])=><button key={id} className={`btn ${section===id?'btn-primary':'btn-ghost'}`} aria-pressed={section===id} onClick={()=>setSection(id)}>{t(label || '')}</button>)}</nav>
    <div className="flex-1 min-h-0 overflow-y-auto"><div hidden={section!=='connections'}><HostsView active={section==='connections'}/></div>{section!=='connections'&&<PersonalComputeView section={section} onManageEnvironment={()=>setSection('environments')}/>}</div>
  </div>;
}
