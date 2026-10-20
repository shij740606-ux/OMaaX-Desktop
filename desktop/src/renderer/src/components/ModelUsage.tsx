import {useEffect,useState} from 'react';
import {useApp} from '../store';

type Session={sessionId:string;projections?:{values?:{tokenUsage?:{uncachedInputTokens?:number;outputTokens?:number;cacheReadTokens?:number}}}};
const tokens=(s?:Session)=>{const u=s?.projections?.values?.tokenUsage;return (u?.uncachedInputTokens??0)+(u?.outputTokens??0)+(u?.cacheReadTokens??0);};

export default function ModelUsage(){
 const {currentSession,dsh}=useApp();
 const [error,setError]=useState('');
 const [usage,setUsage]=useState<{current:number;project:number;all:number}|null>(null);
 useEffect(()=>{
  let live=true;
  setUsage(null);setError('');
  const load=async()=>{
   const errors:string[]=[];
   if(dsh.state==='running'){
    try{
     const [sessions,workspaces]=await Promise.all([
      window.biodsh.dshRpc('session.list') as Promise<{items:Session[]}>,
      window.biodsh.dshRpc('workspace.list') as Promise<{items:{sessionIds:string[]}[]}>,
     ]);
     const project=workspaces.items.find(w=>currentSession&&w.sessionIds.includes(currentSession));
     if(live)setUsage({current:tokens(sessions.items.find(s=>s.sessionId===currentSession)),project:sessions.items.filter(s=>project?.sessionIds.includes(s.sessionId)).reduce((n,s)=>n+tokens(s),0),all:sessions.items.reduce((n,s)=>n+tokens(s),0)});
    }catch{errors.push('用量暂不可用，请稍后重新打开设置。');}
   }
   if(live)setError(errors.join(' '));
  };
  void load();const timer=setInterval(()=>void load(),60000);
  return()=>{live=false;clearInterval(timer);};
 },[currentSession,dsh.state]);
 return <section className="card p-5 flex flex-col gap-3" aria-label="模型实际用量">
  <h2 className="t-headline">模型使用情况</h2>

  {usage?<dl className="grid grid-cols-3 gap-3">{([['本对话',usage.current],['本项目',usage.project],['全部对话',usage.all]] as const).map(([label,value])=><div key={label}><dt className="t-caption">{label}</dt><dd className="t-headline">{value.toLocaleString()} <small>tokens</small></dd></div>)}</dl>:<p className="t-caption">{dsh.state==='running'?'正在读取用量…':'智能体运行后可查看对话用量。'}</p>}
  {error&&<p role="alert">{error}</p>}
  <p className="t-caption">此处为本机保存的对话用量，不代表账号累计用量或余额。</p>
 </section>;
}
