// Company-only model transport. No provider keys or user-selected upstream URLs.
import http from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash, timingSafeEqual} from 'node:crypto';
import {once} from 'node:events';
import {pathToFileURL} from 'node:url';
const exec = promisify(execFile);
export const MAX_REQUEST_BYTES = 1024 * 1024;
const failure = (status, message) => Object.assign(new Error(message), {status});
// The SDK recognizes context overflow on HTTP 400; HTTP 413 is otherwise terminal.
const contextOverflow = () => Object.assign(failure(400,'对话内容较长，正在整理上下文后继续。'), {code:'context_length_exceeded'});
const messages = {401:'登录已失效，请重新登录。',403:'当前账号暂不能使用对话服务，请联系管理员。',429:'请求较多，请稍后重试。'};
const safeError = status => messages[status] || '对话服务暂不可用，请稍后重试。';

export function authorizedModels(list) {
  return Array.isArray(list.models) ? list.models.filter(m => m && m.enabled !== false && typeof m.id==='string' && m.id.trim()) : [];
}
export function modelLimits(model) {
  const positive=value=>Number.isSafeInteger(value)&&value>0?value:undefined;
  // contextLength is the published context window, never a completion budget.
  return {contextWindow:positive(model.contextLength),maxOutputTokens:positive(model.maxOutputTokens)};
}
export function applyOutputLimit(body, model) {
  const {maxOutputTokens}=modelLimits(model);
  if(body.stream===true){
    // Old sessions and SDK defaults must not reintroduce a hard-coded chat cap.
    delete body.max_tokens;delete body.max_completion_tokens;
    if(maxOutputTokens!==undefined)body.max_tokens=maxOutputTokens;
  }else if(maxOutputTokens!==undefined&&Number.isSafeInteger(body.max_tokens)&&body.max_tokens>maxOutputTokens){
    body.max_tokens=maxOutputTokens;
  }
}
export function modeEffort(model, requested) {
  const levels=model.modelEffort?.levels;
  if (!Array.isArray(levels) || !levels.length) {
    if (requested !== undefined) throw failure(400,'当前服务暂不支持选择推理模式，请刷新后重试。');
    return undefined;
  }
  const value=requested ?? model.modelEffort.default;
  if(typeof value!=='string'||!levels.includes(value))throw failure(400,'当前推理模式不可用，请重新选择。');
  return value;
}

export async function startBridge({key, session, endpoint='https://llm.haiguji.com/v1/chat/completions', fetcher=fetch}) {
  let cached, refreshing, active=0;
  const controllers=new Set();
  async function credentials(s, rejectedToken) {
    const identity=createHash('sha256').update(s.hub+'\n'+s.token).digest('hex');
    if(cached?.identity===identity && cached.until>Date.now() && (!rejectedToken || cached.token!==rejectedToken))return cached;
    if(refreshing){await refreshing;return credentials(s);}
    refreshing=(async()=>{
      const call=async(path,post=false)=>{
        let r;
        try {r=await fetcher(s.hub+path,{method:post?'POST':'GET',headers:{Authorization:`Bearer ${s.token}`,...(post?{'Content-Type':'application/json'}:{})},...(post?{body:'{}'}:{}),redirect:'error',signal:AbortSignal.timeout(15000)});}
        catch {throw failure(502,safeError(502));}
        if(!r.ok)throw failure(r.status,safeError(r.status));
        const v=await r.json();if(v.ok!==true)throw failure(502,safeError(502));return v;
      };
      const list=await call('/api/llm/models');
      const models=authorizedModels(list);
      const model=models[0];
      if(!model)throw failure(403,'当前账号尚未开通对话服务，请联系管理员。');
      const v=await call('/api/llm/token',true);
      if(typeof v.token!=='string'||!v.token||!Number.isFinite(v.expires_in)||v.expires_in<=0)throw failure(502,safeError(502));
      cached={identity,model:model.id,models,token:v.token,until:Date.now()+Math.max(0,v.expires_in-30)*1000};
      return cached;
    })();
    try{return await refreshing;}finally{refreshing=null;}
  }
  function reply(res,status,message,code='COMPANY_LLM_ERROR'){res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify({error:{code,message}}));}
  const server=http.createServer(async(req,res)=>{
    const supplied=Buffer.from(req.headers.authorization||'');const expected=Buffer.from(`Bearer ${key}`);
    if(req.headers.origin || supplied.length!==expected.length || !timingSafeEqual(supplied,expected)){reply(res,401,'Unauthorized');return;}
    if(req.method==='GET'&&req.url==='/v1/modes'){
      try {
        const s=await session();if(!s?.token||!s?.hub)throw failure(401,safeError(401));
        const c=await credentials(s),m=c.models[0];
        const levels=m.modelEffort?.levels||[];
        res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});
        res.end(JSON.stringify({levels:['low','high','max'].filter(v=>levels.includes(v)),default:m.modelEffort?.default||null,...modelLimits(m),maxRequestBytes:MAX_REQUEST_BYTES}));
      }catch(e){reply(res,e.status||502,safeError(e.status));}
      return;
    }
    if(req.method!=='POST'||req.url!=='/v1/chat/completions'){reply(res,404,'Not found');return;}
    if(active>=5){reply(res,429,safeError(429));return;}
    active++;const controller=new AbortController();controllers.add(controller);
    const abort=()=>controller.abort();res.on('close',abort);
    let timeout,timeoutMs=180000,watch;
    const resetTimeout=()=>{clearTimeout(timeout);timeout=setTimeout(abort,timeoutMs);};
    resetTimeout();
    try {
      let size=0;const chunks=[];
      for await(const chunk of req){size+=chunk.length;if(size>8*1024*1024)throw contextOverflow();chunks.push(chunk);}
      let body;try{body=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw failure(400,'请求格式无效。');}
      if(!Array.isArray(body.messages)||!body.messages.length)throw failure(400,'对话内容不能为空。');
      const initial=await session();
      if(!initial?.token||!initial?.hub)throw failure(401,safeError(401));
      // Stop an active stream if the native login changes. Never send a new user's token for an old request.
      let checking=false;
      watch=setInterval(async()=>{if(checking)return;checking=true;try{const next=await session();if(next.token!==initial.token||next.hub!==initial.hub)abort();}catch{abort();}finally{checking=false;}},5000);
      // The SDK uses a public local alias, never a provider ID. Each request carries
      // its own mode so simultaneous sessions cannot change each other's requests.
      const alias=/^omaax-(low|high|max)$/.exec(body.model||'');
      const requested=alias?.[1] ?? body.effort;
      let c=await credentials(initial),response;
      for(let attempt=0;attempt<2;attempt++){
        if(controller.signal.aborted)throw failure(408,'请求已取消或超时。');
        const current=await session();if(current.token!==initial.token||current.hub!==initial.hub)throw failure(401,safeError(401));
        // Legacy sessions may carry a provider model ID. The company list remains authoritative.
        body.model=c.model;
        const effort=modeEffort(c.models[0],requested);
        if(effort===undefined)delete body.effort;else body.effort=effort;
        // Include the company default mode when budgeting the initial wait.
        timeoutMs=effort==='max'?600000:effort==='high'?300000:180000;
        resetTimeout();
        // Only the company gateway translates effort to provider-specific fields.
        delete body.reasoning_effort;delete body.thinking;
        applyOutputLimit(body,c.models[0]);
        const payload=JSON.stringify(body);
        if(Buffer.byteLength(payload,'utf8')>MAX_REQUEST_BYTES)throw contextOverflow();
        response=await fetcher(endpoint,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${c.token}`},body:payload,redirect:'error',signal:controller.signal});
        if([400,401,403].includes(response.status)){
          let code;try{code=(await response.json()).error?.code;}catch{}
          if(response.status===400&&['context_length_exceeded','context_window_exceeded'].includes(String(code).toLowerCase()))throw contextOverflow();
          if(attempt===0&&(response.status===401||['MODEL_NOT_FOUND','MODEL_FORBIDDEN'].includes(code))){c=await credentials(initial,c.token);continue;}
        }
        break;
      }
      if(response.status===413)throw contextOverflow();
      if(!response.ok)throw failure(response.status,safeError(response.status));
      const contentType=response.headers.get('content-type')||'';
      if(body.stream===true&&!contentType.includes('text/event-stream'))throw failure(502,safeError(502));
      if(body.stream!==true&&!contentType.includes('application/json'))throw failure(502,safeError(502));
      res.writeHead(200,{'Content-Type':body.stream===true?'text/event-stream; charset=utf-8':'application/json','Cache-Control':'no-store'});
      if(body.stream===true)resetTimeout();
      // Preserve tool-call fragments, reasoning and usage frames; never retry a started stream.
      // Active streams have an idle deadline, not a total generation deadline.
      // Nonstream helpers retain their bounded total request time.
      for await(const chunk of response.body){if(controller.signal.aborted)break;if(body.stream===true&&chunk.length>0)resetTimeout();if(!res.write(chunk))await once(res,'drain',{signal:controller.signal});}
      if(controller.signal.aborted)res.destroy();else res.end();
    } catch(e){if(!res.headersSent&&!res.destroyed)reply(res,e.status||502,e.status?e.message:safeError(502),e.code==='context_length_exceeded'?e.code:undefined);else res.destroy();}
    finally{clearTimeout(timeout);clearInterval(watch);controllers.delete(controller);active--;}
  });
  server.requestTimeout=20000;server.headersTimeout=10000;
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  return {port:server.address().port,close:()=>{cached=null;for(const c of controllers)c.abort();server.closeAllConnections();return new Promise(resolve=>server.close(resolve));}};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  let buffer='',started=false,bridge;
  process.stdin.setEncoding('utf8');
  process.stdin.on('data',async chunk=>{
    if(started)return;buffer+=chunk;if(buffer.length>16384)process.exit(1);if(!buffer.includes('\n'))return;started=true;
    try{
      const {key,executable,root,accountHome}=JSON.parse(buffer.split('\n')[0]);buffer='';
      if(!key||!executable||!root||!accountHome)throw Error();
      const session=async()=>{
        try {const {stdout}=await exec(executable,['--company-llm-session'],{env:{...process.env,BIODSH_HOME:root,OMAAX_ACCOUNT_HOME:accountHome},windowsHide:true,timeout:30000,maxBuffer:16384});return JSON.parse(stdout);}
        catch{throw failure(401,safeError(401));}
      };
      bridge=await startBridge({key,session});process.stdout.write(JSON.stringify({port:bridge.port})+'\n');
    }catch{process.stderr.write('Company model connection could not start\n');process.exit(1);}
  });
  process.stdin.on('end',()=>{void bridge?.close().finally(()=>process.exit(0));if(!bridge)process.exit(0);});
}
