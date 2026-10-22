import ServerDeleteRequests from '../components/ServerDeleteRequests';
import { useEffect, useRef, useState } from 'react';
import { RefreshCw, Play, AlertTriangle } from 'lucide-react';
import { useApp } from '../store';
import {analysisContext,openAnalysisFiles} from './analysisContext';
import { useT } from '../i18n';

function reasoningResponse(value:unknown):{levels:string[];selected?:string} {
  const data=value as {levels?:unknown;selected?:unknown}|null;
  if(!data||!Array.isArray(data.levels)||!data.levels.every(v=>['low','high','max'].includes(v))
    ||(data.selected!==undefined&&(typeof data.selected!=='string'||!data.levels.includes(data.selected))))throw Error('Invalid reasoning modes');
  return {levels:data.levels,selected:data.selected as string|undefined};
}

// 对话页本身不渲染聊天：它只留出一块区域，主进程把 dsh 的网页视图贴在这块区域上。
export default function ChatView() {
  const { dsh, startDsh, restartDsh, credential, setTab, env, settings, overlay, currentSession } = useApp();
  const { t } = useT();
  const [reasoning,setReasoning]=useState<{levels:string[];selected?:string}>({levels:[]});
  const modeSession=useRef(currentSession);modeSession.current=currentSession;
  const [modeBusy,setModeBusy]=useState(false),[modeError,setModeError]=useState('');
  useEffect(()=>{
    let active=true;setReasoning({levels:[]});setModeError('');
    if(currentSession&&dsh.state==='running')void window.biodsh.dshRpc('session.reasoning',{sessionId:currentSession})
      .then(v=>{if(active)setReasoning(reasoningResponse(v));}).catch(()=>{if(active)setModeError('推理模式暂不可用');});
    return()=>{active=false;};
  },[currentSession,dsh.state]);
  const changeMode=async(effort:string)=>{
    const sid=currentSession;setModeBusy(true);setModeError('');
    try{const value=await window.biodsh.dshRpc('session.reasoningSet',{sessionId:sid,effort});if(sid===modeSession.current)setReasoning(reasoningResponse(value));}
    catch{if(sid===modeSession.current)setModeError('模式切换未完成，请重试');}finally{setModeBusy(false);}
  };
  useEffect(()=>window.biodsh.onEvent(event=>{
    if(event.type==='reasoning-request'&&event.sessionId===currentSession&&!modeBusy&&reasoning.levels.includes(event.effort))void changeMode(event.effort);
  }),[currentSession,modeBusy,reasoning]);
  useEffect(()=>{
    const send=()=>void window.biodsh.dshSetReasoning?.({sessionId:currentSession,levels:reasoning.levels,selected:reasoning.selected,busy:modeBusy,error:modeError}).catch(()=>{});
    send();const timer=setInterval(send,1000);return()=>{clearInterval(timer);void window.biodsh.dshSetReasoning?.({sessionId:null}).catch(()=>{});};
  },[currentSession,reasoning,modeBusy,modeError]);
  const [deleteServers,setDeleteServers]=useState<string[]>([]);
  useEffect(()=>{let active=true;setDeleteServers([]);const load=()=>{if(currentSession)void window.biodsh.dshRpc('server.files',{sessionId:currentSession}).then(value=>{if(active)setDeleteServers(value as string[]);}).catch(()=>{});};load();const timer=setInterval(load,10000);return()=>{active=false;clearInterval(timer);};},[currentSession]);
  const overlayOpen = !settings?.onboarded || overlay > 0;
  const areaRef = useRef<HTMLDivElement>(null);
  const [workflow,setWorkflow]=useState<{name:string;version:string;serverId?:string}|null>(null);
  useEffect(()=>{try{setWorkflow(JSON.parse(localStorage.getItem('omaax.workflow-selection.'+currentSession)||'null'));}catch{setWorkflow(null);}},[currentSession]);
  useEffect(()=>{if(currentSession&&dsh.state==='running')void analysisContext(currentSession).catch(()=>{});},[currentSession,dsh.state]);

  useEffect(() => {
    if (dsh.state === 'stopped') void startDsh();
  }, []);

  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    const report = () => {
      const r = el.getBoundingClientRect();
      window.biodsh.dshBounds({ x: r.left, y: r.top, width: r.width, height: r.height }, dsh.state === 'running' && !overlayOpen);
    };
    report();
    const ro = new ResizeObserver(report);
    ro.observe(el);
    window.addEventListener('resize', report);
    return () => { ro.disconnect(); window.removeEventListener('resize', report); window.biodsh.dshBounds({ x: 0, y: 0, width: 0, height: 0 }, false); };
  }, [dsh.state, overlayOpen]);

  return (
    <div className="h-full flex flex-col">
      <header className="drag h-[52px] flex items-center justify-between pl-5 pr-0 hairline-b" style={{ background: 'var(--bg)' }}>
        <div className="flex items-center gap-3">
          <span className="t-title2">{t('对话')}</span>{<button className="btn btn-ghost no-drag" disabled={!currentSession} onClick={()=>void(async()=>{const servers=await window.biodsh.dshRpc('server.files',{sessionId:currentSession}) as string[];openAnalysisFiles(currentSession,servers[0]||workflow?.serverId);setTab('hosts');})().catch(()=>{openAnalysisFiles(currentSession,workflow?.serverId);setTab('hosts');})}>本次分析文件</button>}
          {!env.ready && (
            <button className="no-drag badge" onClick={() => setTab('env')}>{t('计算环境未就绪')}</button>
          )}
        </div>
      </header>
      {workflow&&<div className="flex items-center justify-between px-5 py-2 hairline-b" style={{background:'var(--accent-soft)'}}><span>已选择一体化分析流程：{workflow.name}</span><button className="btn btn-ghost" onClick={()=>setTab('workflows')}>查看流程与确认计划</button></div>}
      {deleteServers.length>0&&<div className="max-h-[35vh] overflow-auto">{deleteServers.map(server=><ServerDeleteRequests key={server} server={server} sessionId={currentSession||undefined}/>)}</div>}
      <div ref={areaRef} className="flex-1 min-h-0 relative" style={{ background: 'var(--surface)' }}>
        {dsh.state !== 'running' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 rise">
            {dsh.state === 'starting' && <><div className="ring spin" style={{ ['--p' as string]: 30 }} /><div className="t-body" style={{ color: 'var(--text-2)' }}>{t('正在启动智能体…')}</div></>}
            {dsh.state === 'error' && (
              <>
                <div className="t-title2">{t('智能体没能启动')}</div>
                <div className="t-body max-w-[420px] text-center" style={{ color: 'var(--text-2)' }}>对话连接未完成，请检查模型设置后重试。</div>
                <button className="btn btn-primary" onClick={() => restartDsh()}><Play size={13} /> {t('再试一次')}</button>
                <details className="max-w-[640px]"><summary>查看诊断日志</summary><pre className="log max-h-[220px] mt-2">{dsh.log.slice(-40).join('\n')}</pre></details>
              </>
            )}
            {dsh.state === 'stopped' && <button className="btn btn-primary btn-lg" onClick={() => startDsh()}><Play size={14} /> {t('启动智能体')}</button>}
          </div>
        )}
      </div>
    </div>
  );
}
