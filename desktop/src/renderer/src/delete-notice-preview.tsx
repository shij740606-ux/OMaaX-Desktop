// Test-only entry; all operations are synthetic and cannot reach a server.
import React from 'react';
import {createRoot} from 'react-dom/client';
import ServerDeleteRequests from './components/ServerDeleteRequests';
import './styles.css';
const calls:{action:string;body:unknown}[]=[];
let rows=[{id:'test',digest:'snapshot',policy:'notice-v1',paths:['/test-only/result.tmp'],snapshot:{bytes:7,entries:1},warnings:[],context:{sessionId:'test-session',projectName:'测试项目',sessionName:'测试对话'}}];
(window as any).__noticeCalls=calls;
window.biodsh={personalCompute:async(_server:string,action:string,body:unknown)=>{
 calls.push({action,body});
 if(action==='server_delete_requests')return rows;
 if(action==='server_delete_notified'||action==='server_delete_reject'){rows=[];return {recoverable:true};}
 throw Error('Unexpected test action');
}} as unknown as typeof window.biodsh;
createRoot(document.getElementById('root')!).render(<main style={{padding:24}}>
 {location.search.includes('offscreen')&&<div style={{height:1800}}>滚动到删除提醒</div>}
 <ServerDeleteRequests server="test-only" sessionId="test-session"/>
</main>);
