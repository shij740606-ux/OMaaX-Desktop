import {test} from 'node:test';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
const {startBridge,modeEffort,modelLimits,MAX_REQUEST_BYTES}=await import(process.env.OMAAX_TEST_COMPANY_BRIDGE ? pathToFileURL(path.resolve(process.env.OMAAX_TEST_COMPANY_BRIDGE)).href : new URL('../resources/scripts/company-llm.mjs',import.meta.url).href);
const auth={hub:'https://auth.test',token:'USER_SECRET'};
const json=v=>new Response(JSON.stringify(v),{headers:{'content-type':'application/json'}});
const modernModel={id:'cloud-model',modelEffort:{levels:['low','high','max'],default:'high'}};

test('model context and completion capacities are separate and validated',()=>{
 assert.deepEqual(modelLimits({contextLength:1048576}),{contextWindow:1048576,maxOutputTokens:undefined});
 assert.deepEqual(modelLimits({contextLength:1048576,maxOutputTokens:131072}),{contextWindow:1048576,maxOutputTokens:131072});
 for(const invalid of [0,-1,1.5,'1048576',Infinity,Number.MAX_SAFE_INTEGER+1]){
  assert.deepEqual(modelLimits({contextLength:invalid,maxOutputTokens:invalid}),{contextWindow:undefined,maxOutputTokens:undefined});
 }
});
const sse=()=>new Response('data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
test('1M context with no advertised output cap delegates chat output to the service, including old sessions',()=>fixture(async({bridge,request,calls})=>{
 const caps=await fetch(`http://127.0.0.1:${bridge.port}/v1/modes`,{headers:{Authorization:'Bearer local-secret'}}).then(r=>r.json());
 assert.equal(caps.contextWindow,1048576);assert.ok(!('maxOutputTokens' in caps));assert.equal(caps.maxRequestBytes,1048576);
 for(const cap of [8192,256000]){
  assert.equal((await request({stream:true,max_tokens:cap,max_completion_tokens:cap})).status,200);
  const body=JSON.parse(calls.at(-1).body);assert.ok(!('max_tokens' in body));assert.ok(!('max_completion_tokens' in body));
 }
},{models:[{...modernModel,contextLength:1048576}],respond:sse}));
test('all chat modes use the advertised output cap instead of a saved SDK cap',()=>fixture(async({request,calls})=>{
 for(const mode of ['low','high','max'])assert.equal((await request({model:'omaax-'+mode,stream:true,max_tokens:8192})).status,200);
 assert.ok(calls.every(c=>JSON.parse(c.body).max_tokens===131072));
},{models:[{...modernModel,contextLength:1048576,maxOutputTokens:131072}],respond:sse}));
test('short nonstream helper requests retain their requested output budget',()=>fixture(async({request,calls})=>{
 await request({max_tokens:400});await request({max_tokens:8000});await request({max_tokens:200000});
 assert.deepEqual(calls.map(c=>JSON.parse(c.body).max_tokens),[400,8000,131072]);
},{models:[{...modernModel,maxOutputTokens:131072}]}));
test('new API permits omitted enabled and maps each public mode independently',()=>fixture(async({request,calls})=>{
 const responses=await Promise.all(['low','high','max'].map(v=>request({model:'omaax-'+v})));
 assert.deepEqual(responses.map(r=>r.status),[200,200,200]);
 const bodies=calls.map(c=>JSON.parse(c.body));
 assert.deepEqual(bodies.map(b=>b.effort).sort(),['high','low','max']);
 assert.ok(bodies.every(b=>b.model==='cloud-model'&&!('thinking' in b)&&!('reasoning_effort' in b)));
},{models:[modernModel]}));
test('server default effort and unavailable effort are validated',()=>{
 assert.equal(modeEffort(modernModel), 'high');
 assert.throws(()=>modeEffort(modernModel,'medium'));
 assert.throws(()=>modeEffort({id:'legacy'},'max'));
});
test('unavailable mode is rejected before model invocation',()=>fixture(async({request,calls})=>{
 assert.equal((await request({model:'omaax-max'})).status,400);
 assert.equal(calls.length,0);
},{models:[{id:'restricted',modelEffort:{levels:['low'],default:'low'}}]}));
async function fixture(fn,options={}){
 let mints=0,lists=0,calls=[];
 const fetcher=async(url,init)=>{
  if(url.endsWith('/api/llm/models')){lists++;assert.equal(init.headers.Authorization,'Bearer USER_SECRET');return json({ok:true,models:options.models??[{id:'company-default',enabled:true}]});}
  if(url.endsWith('/api/llm/token')){mints++;return json({ok:true,token:'PROXY_'+mints,expires_in:options.ttl??600});}
  calls.push({body:JSON.parse(init.body),...init});assert.match(init.headers.Authorization,/Bearer PROXY_/);return options.respond?.(calls.length,init)||json({choices:[{message:{content:'answer'}}],usage:{total_tokens:3}});
 };
 const bridge=await startBridge({key:'local-secret',session:options.session||(()=>auth),fetcher});
 const request=(body={},headers={})=>fetch(`http://127.0.0.1:${bridge.port}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer local-secret',...headers},body:JSON.stringify({model:'private-old-model',messages:[{role:'user',content:'hello'}],...body})});
 try{await fn({bridge,request,calls,counts:()=>({mints,lists})});}finally{await bridge.close();}
}
test('company default overrides legacy model; user token never reaches forwarder; usage retained',()=>fixture(async({request,calls,counts})=>{
 const r=await request();assert.equal(r.status,200);assert.equal((await r.json()).usage.total_tokens,3);
 const body=JSON.parse(calls[0].body);assert.equal(body.model,'company-default');assert.deepEqual(body.messages,[{role:'user',content:'hello'}]);
 await (await request()).text();assert.deepEqual(counts(),{mints:1,lists:1});
}));
test('reject unknown local token before signing',()=>fixture(async({request,counts})=>{assert.equal((await request({}, {Authorization:'Bearer wrong'})).status,401);assert.equal(counts().mints,0);}));
test('reject browser-origin requests',()=>fixture(async({request,counts})=>{assert.equal((await request({}, {Origin:'https://evil.test'})).status,401);assert.equal(counts().mints,0);}));
test('empty model grant never falls back to personal key',()=>fixture(async({request,calls})=>{assert.equal((await request()).status,403);assert.equal(calls.length,0);},{models:[]}));
test('expired proxy refreshes once, preserving request',()=>fixture(async({request,counts})=>{assert.equal((await request()).status,200);assert.equal(counts().mints,2);},{respond:n=>n===1?new Response(JSON.stringify({error:{code:'UNAUTHORIZED'}}),{status:401}):undefined}));
test('repeated 401 stops after one retry',()=>fixture(async({request,calls})=>{assert.equal((await request()).status,401);assert.equal(calls.length,2);},{respond:()=>new Response('{}',{status:401})}));
test('revoked model refreshes grants and token once',()=>fixture(async({request,counts})=>{assert.equal((await request()).status,200);assert.equal(counts().lists,2);},{respond:n=>n===1?new Response(JSON.stringify({error:{code:'MODEL_FORBIDDEN'}}),{status:403}):undefined}));
test('429 does not replay billable requests or leak upstream error',()=>fixture(async({request,calls})=>{const r=await request();assert.equal(r.status,429);assert.doesNotMatch(await r.text(),/PROVIDER_SECRET/);assert.equal(calls.length,1);},{respond:()=>new Response('PROVIDER_SECRET',{status:429})}));
test('502 never blindly retries',()=>fixture(async({request,calls})=>{assert.equal((await request()).status,502);assert.equal(calls.length,1);},{respond:()=>new Response('private',{status:502})}));

test('oversized requests signal recoverable context overflow without forwarding or truncating',async t=>{
 for(const [name,content] of [['ASCII','x'.repeat(MAX_REQUEST_BYTES)],['Chinese','原始数据'.repeat(100000)],['emoji','🧬'.repeat(300000)]]){
  await t.test(name,()=>fixture(async({request,calls})=>{
   const body={stream:true,messages:[{role:'user',content}]};
   if(name!=='ASCII')assert.ok(JSON.stringify(body).length<MAX_REQUEST_BYTES);
   assert.ok(Buffer.byteLength(JSON.stringify(body),'utf8')>MAX_REQUEST_BYTES);
   const response=await request(body);assert.equal(response.status,400);
   const result=await response.json();assert.equal(result.error.code,'context_length_exceeded');
   assert.doesNotMatch(JSON.stringify(result),/USER_SECRET|PROXY_|原始数据|🧬|新建对话/);
   assert.equal(calls.length,0);
  },{respond:sse}));
 }
});

test('the final UTF-8 wire body includes model, effort, tools and output limits in the budget',()=>fixture(async({request,calls})=>{
 const body={model:'x',stream:true,messages:[{role:'user',content:''}],tools:[{type:'function',function:{name:'inspect',parameters:{type:'object'}}}]};
 const final={...body,model:modernModel.id,effort:'high',max_tokens:131072};
 body.messages[0].content='x'.repeat(MAX_REQUEST_BYTES-Buffer.byteLength(JSON.stringify(final),'utf8'));
 assert.ok(Buffer.byteLength(JSON.stringify(body),'utf8')<MAX_REQUEST_BYTES);
 const first=await request(body);assert.equal(first.status,200);await first.text();
 assert.equal(Buffer.byteLength(calls[0].body,'utf8'),MAX_REQUEST_BYTES);
 assert.deepEqual(JSON.parse(calls[0].body).messages,body.messages);
 assert.deepEqual(JSON.parse(calls[0].body).tools,body.tools);
 body.messages[0].content+='x';
 const second=await request(body);assert.equal(second.status,400);
 assert.equal((await second.json()).error.code,'context_length_exceeded');assert.equal(calls.length,1);
},{models:[{...modernModel,maxOutputTokens:131072}],respond:sse}));

test('upstream HTML 413 becomes recoverable context overflow without leaking or replaying',()=>fixture(async({request,calls})=>{
 const response=await request({stream:true});assert.equal(response.status,400);
 const result=await response.json();assert.equal(result.error.code,'context_length_exceeded');
 assert.doesNotMatch(JSON.stringify(result),/PROVIDER_SECRET|openresty|<html>|USER_SECRET|PROXY_/);
 assert.equal(calls.length,1);
},{respond:()=>new Response('<html>413 Request Entity Too Large openresty PROVIDER_SECRET</html>',{status:413,headers:{'content-type':'text/html'}})}));

test('known upstream context codes survive redaction but unrelated 400 errors do not trigger compaction',async()=>{
 for(const code of ['context_length_exceeded','CONTEXT_WINDOW_EXCEEDED','INVALID_PARAMETER']){
  await fixture(async({request,calls})=>{
   const response=await request();assert.equal(response.status,400);
   const result=await response.json();
   assert.equal(result.error.code,code==='INVALID_PARAMETER'?'COMPANY_LLM_ERROR':'context_length_exceeded');
   assert.doesNotMatch(JSON.stringify(result),/PROVIDER_SECRET/);assert.equal(calls.length,1);
  },{respond:()=>new Response(JSON.stringify({error:{code,message:'PROVIDER_SECRET'}}),{status:400})});
 }
});

test('the local receiver safety ceiling also preserves the context recovery protocol',()=>fixture(async({request,calls,counts})=>{
 const response=await request({messages:[{role:'user',content:'x'.repeat(8*1024*1024)}]});
 assert.equal(response.status,400);assert.equal((await response.json()).error.code,'context_length_exceeded');
 assert.equal(calls.length,0);assert.equal(counts().mints,0);
}));
test('malformed body rejected',()=>fixture(async({request,calls})=>{assert.equal((await request({messages:null})).status,400);assert.equal(calls.length,0);}));
test('logout prevents forwarding',()=>fixture(async({request,calls})=>{assert.equal((await request()).status,502);assert.equal(calls.length,0);},{session:()=>{throw Error('private');}}));
test('renew expired cached credentials',()=>fixture(async({request,counts})=>{await(await request()).text();await(await request()).text();assert.equal(counts().mints,2);},{ttl:1}));
test('concurrent signing is coalesced',()=>fixture(async({request,counts})=>{await Promise.all(Array.from({length:4},async()=>assert.equal((await request()).status,200)));assert.equal(counts().mints,1);}));
test('tool-call SSE and usage pass intact; reasoning tiers not added',()=>fixture(async({request,calls})=>{
 const r=await request({stream:true,tools:[{type:'function',function:{name:'test',parameters:{type:'object'}}}],reasoning_effort:'high'});const text=await r.text();assert.match(text,/tool_calls/);assert.match(text,/total_tokens/);assert.match(text,/\[DONE\]/);assert.ok(!('reasoning_effort' in JSON.parse(calls[0].body)));assert.equal(JSON.parse(calls[0].body).tools[0].function.name,'test');
},{respond:()=>new Response('data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call1","type":"function","function":{"name":"test","arguments":"{}"}}]}}]}\n\ndata: {"choices":[],"usage":{"total_tokens":4}}\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}})}));
test('cancelled stream aborts upstream',()=>{let aborted=false;return fixture(async({bridge,request})=>{
 const controller=new AbortController();const r=await fetch(`http://127.0.0.1:${bridge.port}/v1/chat/completions`,{method:'POST',headers:{Authorization:'Bearer local-secret'},body:JSON.stringify({messages:[{role:'user',content:'hello'}],stream:true}),signal:controller.signal});
 await r.body.getReader().read();controller.abort();await new Promise(r=>setTimeout(r,100));assert.equal(aborted,true);
},{respond:(_n,init)=>new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {}\n\n'));init.signal.addEventListener('abort',()=>{aborted=true;c.close();},{once:true});}}),{headers:{'content-type':'text/event-stream'}})});});
test('simultaneous expired-token failures reuse one refreshed token',()=>fixture(async({request,counts})=>{
 await Promise.all(Array.from({length:4},async()=>assert.equal((await request()).status,200)));assert.equal(counts().mints,2);
},{respond:(_n,init)=>init.headers.Authorization==='Bearer PROXY_1'?new Response('{}',{status:401}):undefined}));
test('login change aborts an active upstream stream',()=>{let current={...auth},aborted=false;return fixture(async({request})=>{
 const r=await request({stream:true});const reader=r.body.getReader();await reader.read();current={...auth,token:'another-user'};
 await assert.rejects(async()=>{while(!(await reader.read()).done){}});assert.equal(aborted,true);
},{session:()=>current,respond:(_n,init)=>new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {}\n\n'));init.signal.addEventListener('abort',()=>{aborted=true;c.close();},{once:true});}}),{headers:{'content-type':'text/event-stream'}})});});
test('stream transport failure never replays a partial response',()=>fixture(async({request,calls})=>{
 const r=await request({stream:true});await assert.rejects(()=>r.text());assert.equal(calls.length,1);
},{respond:()=>new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {}\n\n'));setTimeout(()=>c.error(Error('network lost')),20);}}),{headers:{'content-type':'text/event-stream'}})}));

test('initial wait uses the resolved company default effort',async t=>{
 const schedule=globalThis.setTimeout;let lastDelay;
 t.mock.method(globalThis,'setTimeout',(callback,delay,...args)=>{lastDelay=delay;return schedule(callback,delay,...args);});
 for(const [effort,expected] of [['low',180000],['high',300000],['max',600000]]){
  await fixture(async({request})=>{assert.equal((await request({model:'omaax',stream:true})).status,200);},
   {models:[{...modernModel,modelEffort:{levels:['low','high','max'],default:effort}}],respond:(_n,init)=>{
    assert.equal(JSON.parse(init.body).effort,effort);assert.equal(lastDelay,expected);return sse();
   }});
 }
});

test('stream deadlines measure idle time; helpers and stalled streams remain bounded',{timeout:10000},async t=>{
 const schedule=globalThis.setTimeout;
 // Compress only the bridge's production deadlines; keep real HTTP and stream timing.
 t.mock.method(globalThis,'setTimeout',(callback,delay,...args)=>schedule(callback,[180000,300000,600000].includes(delay)?200:delay,...args));
 await t.test('continuous output can outlast the initial deadline without replay',async()=>{
  let interrupted=false;
  await fixture(async({request,calls})=>{
   const response=await request({model:'omaax',stream:true});const body=await response.text();
   assert.match(body,/segment 24/);assert.match(body,/\[DONE\]/);assert.equal(interrupted,false);assert.equal(calls.length,1);
  },{models:[modernModel],respond:(_n,init)=>new Response(new ReadableStream({start(controller){
   let count=0;
   const stop=()=>{interrupted=true;clearInterval(ticker);controller.close();};
   const ticker=setInterval(()=>{
    count++;controller.enqueue(new TextEncoder().encode('data: '+JSON.stringify({choices:[{delta:{content:`segment ${count}`},finish_reason:null}]})+'\n\n'));
    if(count===24){clearInterval(ticker);init.signal.removeEventListener('abort',stop);controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'));controller.close();}
   },25);
   init.signal.addEventListener('abort',stop,{once:true});
  }}),{headers:{'content-type':'text/event-stream'}})});
 });
 await t.test('a stream that goes silent is cancelled once',async()=>{
  let interrupted=false;
  await fixture(async({request,calls})=>{
   const response=await request({stream:true});await assert.rejects(()=>response.text());
   assert.equal(interrupted,true);assert.equal(calls.length,1);
  },{respond:(_n,init)=>new Response(new ReadableStream({start(controller){
   controller.enqueue(new TextEncoder().encode('data: {"choices":[{"delta":{"content":"started"}}]}\n\n'));
   init.signal.addEventListener('abort',()=>{interrupted=true;controller.close();},{once:true});
  }}),{headers:{'content-type':'text/event-stream'}})});
 });
 await t.test('a nonstream helper keeps its total deadline even while bytes arrive',async()=>{
  let interrupted=false;
  await fixture(async({request,calls})=>{
   const response=await request({stream:false});await assert.rejects(()=>response.text());
   assert.equal(interrupted,true);assert.equal(calls.length,1);
  },{respond:(_n,init)=>new Response(new ReadableStream({start(controller){
   const ticker=setInterval(()=>controller.enqueue(new TextEncoder().encode(' ')),25);
   init.signal.addEventListener('abort',()=>{interrupted=true;clearInterval(ticker);controller.close();},{once:true});
  }}),{headers:{'content-type':'application/json'}})});
 });
 await t.test('no response headers cannot wait indefinitely',async()=>{
  let interrupted=false;
  await fixture(async({request,calls})=>{
   const response=await request({stream:true});assert.equal(response.status,502);
   assert.equal(interrupted,true);assert.equal(calls.length,1);
  },{respond:(_n,init)=>new Promise((_resolve,reject)=>init.signal.addEventListener('abort',()=>{interrupted=true;reject(new Error('fixture aborted'));},{once:true}))});
 });
});
