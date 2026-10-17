import {useEffect,useState} from 'react';
import {useApp} from '../store';

type Session={sessionId:string;projections?:{values?:{tokenUsage?:{uncachedInputTokens?:number;outputTokens?:number;cacheReadTokens?:number}}}};
const tokens=(s?:Session)=>{const u=s?.projections?.values?.tokenUsage;return (u?.uncachedInputTokens??0)+(u?.outputTokens??0)+(u?.cacheReadTokens??0);};

export default function ModelUsage(){
 const {settings,currentSession,dsh}=useApp();
 const [balance,setBalance]=useState(''),[error,setError]=useState('');
 const [usage,setUsage]=useState<{current:number;project:number;all:number}|null>(null);
 useEffect(()=>{
  let live=true;
  setUsage(null);setBalance('');setError('');
  const load=async()=>{
   const errors:string[]=[];
   if(settings?.mode!=='offline'){
    try{const value=await window.biodsh.deepseekBalance() as {balance_infos?:{currency:string;total_balance:string}[]};if(live){const b=value.balance_infos?.[0];setBalance(b?`${b.currency} ${b.total_balance}`:'暂无余额信息');}}
    catch{if(live)setBalance('余额暂不可用，请检查模型配置。');}
   }
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
 },[currentSession,dsh.state,settings?.mode]);
 return <section className="card p-5 flex flex-col gap-3" aria-label="模型实际用量">
  <h2 className="t-headline">模型使用情况</h2>
  <p className="t-caption">{settings?.mode==='offline'?'当前使用本地／内网模型。':'DeepSeek 可用余额：'+(balance||'正在查询…')}</p>
  {usage?<dl className="grid grid-cols-3 gap-3">{([['本对话',usage.current],['本项目',usage.project],['全部对话',usage.all]] as const).map(([label,value])=><div key={label}><dt className="t-caption">{label}</dt><dd className="t-headline">{value.toLocaleString()} <small>tokens</small></dd></div>)}</dl>:<p className="t-caption">{dsh.state==='running'?'正在读取用量…':'智能体运行后可查看对话用量。'}</p>}
  {error&&<p role="alert">{error}</p>}
  <p className="t-caption">用量包含缓存命中，以模型返回的记录为准；实际费用以服务商账单为准。</p>
 </section>;
}
