import omaaxIcon from './assets/omaax-icon.png';
import './AuthGate.css';
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
 return <div className="auth-page"><div className="auth-window-controls"><WindowControls compact/></div><form className="auth-card" aria-label="登录 OMaaX" onSubmit={async e=>{e.preventDefault();revision.current++;changing.current=true;setBusy(true);setError('');edited.current=true;const secret=password;setPassword('');try{await window.biodsh.hubLogin(hub,username,secret,remember);setSession(await window.biodsh.hubStatus());}catch(e){setError(loginError(e));}finally{revision.current++;changing.current=false;setBusy(false);}}}>
 <header className="auth-heading"><img src={omaaxIcon} alt="" width={64} height={64}/><div className="auth-wordmark">OMaaX</div><h1>欢迎回来</h1></header>
 <label className="auth-field">用户名<input aria-label="用户名" className="auth-input" placeholder="请输入用户名" autoComplete="username" value={username} onChange={e=>{edited.current=true;setUsername(e.target.value);}} required disabled={busy}/></label>
 <label className="auth-field">密码<input aria-label="密码" className="auth-input" type="password" placeholder="请输入密码" autoComplete="current-password" value={password} onChange={e=>{edited.current=true;setPassword(e.target.value);}} required disabled={busy}/></label>
 <label className="auth-remember"><input type="checkbox" checked={remember} disabled={busy} onChange={async e=>{edited.current=true;const checked=e.target.checked;setRemember(checked);if(!checked){setBusy(true);try{await window.biodsh.hubForgetLogin();}catch(e){setRemember(true);setError(loginError(e));}finally{setBusy(false);}}}}/>记住账号和密码</label>
 {error&&!error.includes('请登录')&&<p role="alert" className="auth-error">{error}</p>}
 <button className="auth-submit" disabled={busy||loading}>{loading?'正在检查登录状态…':busy?'正在登录…':'登录'}</button>
 </form></div>;
}
