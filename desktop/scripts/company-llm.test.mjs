import {test} from 'node:test';
import assert from 'node:assert/strict';
import {startBridge,modeEffort} from '../resources/scripts/company-llm.mjs';
const auth={hub:'https://auth.test',token:'USER_SECRET'};
const json=v=>new Response(JSON.stringify(v),{headers:{'content-type':'application/json'}});
const modernModel={id:'cloud-model',modelEffort:{levels:['low','high','max'],default:'high'}};
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
