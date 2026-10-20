import {createContext,useContext,useEffect,useState,useRef,type ReactNode} from 'react';
import {WindowControls} from './components/Sidebar';
export interface HubUser {id:string;username:string;role:string;authorizations:string[]}
export interface HubStatus {authenticated:boolean;localAccess?:boolean;hub:string;user?:HubUser;error?:string}
function loginError(value:unknown){const text=String(value).replace(/^(?:Error:|UNAUTHORIZED:|FORBIDDEN:)\s*/g,'').trim();return /[\u4e00-\u9fff]/.test(text)&&text.length<180&&!/[\r\n]|Traceback|stack|\{/.test(text)?text:'操作未完成，请重试或联系管理员';}
const AuthContext=createContext<HubStatus & {logout?:()=>Promise<void>}>({authenticated:false,hub:''});
export const useAuth=()=>useContext(AuthContext);
export const hasUnit=(user:HubUser|undefined,unit:string)=>!!user&&(['bioflow','docgen'].includes(unit)||user.role==='admin'||user.authorizations.includes(unit));
export default function AuthGate({children}:{children:ReactNode}) {
 const [session,setSession]=useState<HubStatus>({authenticated:false,hub:''});
 const [hub,setHub]=useState('');const [username,setUsername]=useState('');const [password,setPassword]=useState('');
 const [busy,setBusy]=useState(false);const [loading,setLoading]=useState(true);const [error,setError]=useState('');
 const revision=useRef(0);const changing=useRef(false);
 const [remember,setRemember]=useState(false);const edited=useRef(false);
 useEffect(()=>{let alive=true;void window.biodsh.hubRememberedLogin?.().then(value=>{
  if(alive&&!edited.current&&value){setUsername(value.username);setPassword(value.password);setRemember(true);}
 }).catch(()=>{if(alive)setError('无法读取保存的登录信息，请手动输入');});return()=>{alive=false;};},[]);
 useEffect(()=>{let alive=true;let pending=false;
  const refresh=async()=>{if(pending||changing.current)return;pending=true;const version=revision.current;try{const s=await window.biodsh.hubStatus();if(alive&&version===revision.current&&!changing.current){setSession(s);setHub(h=>h||s.hub);setError(s.error?loginError(s.error):'');}}catch{if(alive&&version===revision.current&&!changing.current){setSession({authenticated:false,hub:''});setError('登录服务不可用，请重试或联系管理员');}}finally{pending=false;if(alive)setLoading(false);}};
  void refresh();const timer=setInterval(refresh,15000);return()=>{alive=false;clearInterval(timer);};
 },[]);
 const logout=async()=>{revision.current++;changing.current=true;setBusy(true);setSession({...session,authenticated:false});try{await window.biodsh.hubLogout();}catch(e){setError(loginError(e));}finally{revision.current++;changing.current=false;setBusy(false);}};
 if(session.authenticated&&session.user)return <AuthContext.Provider value={{...session,logout}}>{children}</AuthContext.Provider>;
 return <div className="h-full w-full flex items-center justify-center" style={{background:'#f5f7fa',color:'#202530'}}><div className="absolute top-3 right-3"><WindowControls compact/></div><form style={{width:420,padding:32,background:'white',borderRadius:16,boxShadow:'0 12px 40px #0001'}} onSubmit={async e=>{e.preventDefault();revision.current++;changing.current=true;setBusy(true);setError('');edited.current=true;const secret=password;setPassword('');try{await window.biodsh.hubLogin(hub,username,secret,remember);setSession(await window.biodsh.hubStatus());}catch(e){setError(loginError(e));}finally{revision.current++;changing.current=false;setBusy(false);}}}>
 <h1 className="text-2xl font-semibold mb-2">登录 OMaaX</h1><p className="text-sm text-gray-500 mb-5">账号由管理员创建，请使用用户名和密码登录。</p>
 <label className="block text-sm mb-3">用户名<input aria-label="用户名" className="block border rounded p-2 mt-1 w-full" autoComplete="username" value={username} onChange={e=>{edited.current=true;setUsername(e.target.value);}} required disabled={busy}/></label>
 <label className="block text-sm mb-3">密码<input aria-label="密码" className="block border rounded p-2 mt-1 w-full" type="password" autoComplete="current-password" value={password} onChange={e=>{edited.current=true;setPassword(e.target.value);}} required disabled={busy}/></label>
 <label className="flex items-center gap-2 text-sm mb-4"><input type="checkbox" checked={remember} disabled={busy} onChange={async e=>{edited.current=true;const checked=e.target.checked;setRemember(checked);if(!checked){setBusy(true);try{await window.biodsh.hubForgetLogin();}catch(e){setRemember(true);setError(loginError(e));}finally{setBusy(false);}}}}/>记住账号和密码</label>
 <p className="text-xs text-gray-500 mb-4">每次打开仍需点击登录。请仅在自己的电脑上记住密码。</p>
 {error&&!error.includes('请登录')&&<p role="alert" className="text-sm text-red-700 mb-3">{error}</p>}
 <button className="w-full rounded p-2 text-white bg-blue-600 disabled:opacity-50" disabled={busy||loading}>{loading?'正在检查登录状态…':busy?'正在登录…':'登录'}</button>
 <p className="text-xs text-gray-500 mt-4">请向管理员获取内测账号。</p>
 </form></div>;
}
