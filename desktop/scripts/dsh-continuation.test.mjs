// Exercise the shipped DSH agent, HTTP adapter and durable sessions, against a
// synthetic local SSE server. No live credentials, data, or model charges.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, realpathSync } from 'node:fs';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { once } from 'node:events';
import { patchContinuation } from './patch-dsh-continuation.mjs';
import { patchCompaction } from './patch-dsh-compaction.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runtime = process.env.OMAAX_TEST_DSH_RUNTIME ? path.resolve(process.env.OMAAX_TEST_DSH_RUNTIME) : path.join(desktop, 'dsh-runtime');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const sse = (delta, finish = 'stop') => 'data: ' + JSON.stringify({id:'fixture',choices:[{index:0,delta,finish_reason:null}]}) + '\n\ndata: ' + JSON.stringify({choices:[{index:0,delta:{},finish_reason:finish}],usage:{prompt_tokens:20,completion_tokens:4,total_tokens:24}}) + '\n\ndata: [DONE]\n\n';

test('continuation patch is idempotent', () => {
  const files = patchContinuation(runtime);
  const before = files.map(f => readFileSync(f, 'utf8'));
  patchContinuation(runtime);
  patchCompaction(runtime);
  assert.deepEqual(files.map(f => readFileSync(f, 'utf8')), before);
});

test('real DSH automatically continues output limits, preserves history and honors stop/errors', {timeout:120000}, async t => {
  const home = mkdtempSync(path.join(tmpdir(), 'omaax-continue-'));
  const workspace = path.join(home, 'workspace'); mkdirSync(workspace);
  const counts = new Map(), requests = new Map(), sessions = new Map(); let pendingCancel, pendingCompact;
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const part of req) raw += part;
    const body = JSON.parse(raw);
    if (body.stream !== true) { res.writeHead(200, {'Content-Type':'application/json'}); res.end(JSON.stringify({choices:[{message:{role:'assistant',content:'Fixture title'},finish_reason:'stop'}]})); return; }
    const match = JSON.stringify(body.messages).match(/CASE:(\w+)/);
    if (match && JSON.stringify(body.messages).includes('You are now acting as a compaction engine')) {
      const mode = match[1] + '_summary', count = (counts.get(mode) ?? 0) + 1;
      counts.set(mode,count); requests.set(mode,[...requests.get(mode) ?? [],body]);
      res.writeHead(200, {'Content-Type':'text/event-stream'});
      if (mode === 'oversized_summary') {res.end(sse({content:'CASE:oversized Completed tool: todo list already updated exactly once. Server job running-175 already submitted; query it before any resume. Continue the remaining task.'}));return;}
      if (mode === 'compact_cancel_summary' && count === 2) {res.write('data: '+JSON.stringify({choices:[{index:0,delta:{content:'waiting'},finish_reason:null}]})+'\n\n');pendingCompact=res;return;}
      if (mode === 'compact_empty_summary') {res.end(sse({},'length'));return;}
      res.end(sse({content:count===1?'## Primary Request\nKeep the fixture history.\n':count===2?'## Progress\nAnalysis completed.\n':'## Next Steps\nAnswer the next request.\n'},count<3?'length':'stop'));
      return;
    }
    if (!match || !body.tools?.length) { res.writeHead(200, {'Content-Type':'text/event-stream'}); res.end(sse({content:'Fixture title'})); return; }
    const mode = match[1], count = (counts.get(mode) ?? 0) + 1;
    counts.set(mode, count); requests.set(mode, [...requests.get(mode) ?? [], body]);
    if (mode === 'forbidden' || mode === 'retry_exhausted' || mode === 'retry_cancel' || (mode === 'retry_ok' && count === 1)) {
      res.writeHead(mode==='forbidden'?403:503, {'Content-Type':'application/json'}); res.end(JSON.stringify({error:{message:'synthetic provider failure'}})); return;
    }
    if (mode === 'error') { res.writeHead(400, {'Content-Type':'application/json'}); res.end(JSON.stringify({error:{message:'fixture rejection'}})); return; }
    res.writeHead(200, {'Content-Type':'text/event-stream'});
    if (mode === 'cancel' && count === 2) { res.write('data: '+JSON.stringify({choices:[{index:0,delta:{content:'in flight'},finish_reason:null}]})+'\n\n'); pendingCancel = res; return; }
    if (mode === 'empty') { res.end(sse({}, 'length')); return; }
    if (mode === 'repeated') { res.end(sse({content:'Identical response'}, 'length')); return; }
    if (mode === 'reasoning') {
      res.end(sse(count <= 3 ? {reasoning_content:`Reasoning segment ${count}`} : {content:'Reasoning completed; final answer'}, count <= 3 ? 'length' : 'stop'));
      return;
    }
    if (mode === 'tool') {
      if (count === 1) res.end(sse({tool_calls:[{index:0,id:'cut',type:'function',function:{name:'todo_write',arguments:'{"todos":['}}]}, 'length'));
      else if (count === 2) res.end(sse({tool_calls:[{index:0,id:'complete',type:'function',function:{name:'todo_write',arguments:JSON.stringify({todos:[{content:'Fixture completed',status:'completed'}]})}}]}, 'tool_calls'));
      else res.end(sse({content:'Tool finished once'}));
      return;
    }
    if (mode === 'done_tool') {
      if (count === 1) res.end(sse({tool_calls:[{index:0,id:'once',type:'function',function:{name:'todo_write',arguments:JSON.stringify({todos:[{content:'Completed before truncation',status:'completed'}]})}}]},'tool_calls'));
      else res.end(sse({content:`done tool segment ${count}`},count===2?'length':'stop'));
      return;
    }
    if (mode === 'oversized') {
      if (count === 1) res.end(sse({tool_calls:[{index:0,id:'big-once',type:'function',function:{name:'todo_write',arguments:JSON.stringify({todos:[{content:'Already submitted running-175',status:'completed'}]})}}]},'tool_calls'));
      else if (count === 2) res.end(sse({content:'CASE:oversized Preserve original provenance and task running-175. '.repeat(19000)},'length'));
      else res.end(sse({content:'Recovered automatically; remaining task completed without repeating the completed tool.'}));
      return;
    }
    if (mode === 'normal') { res.end(sse({content:'Normal answer'})); return; }
    if (mode === 'retry_ok') { res.end(sse({content:'Recovered after one temporary error'})); return; }
    if (mode.startsWith('compact_')) { res.end(sse({content:'History fixture answer completed'})); return; }
    const limit = mode === 'long' ? 6 : 1;
    res.end(sse({content:`${mode} segment ${count}`}, count <= limit ? 'length' : 'stop'));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let companyBridge;
  if (process.env.OMAAX_TEST_COMPANY_BRIDGE) {
    const {startBridge} = await import(pathToFileURL(path.resolve(process.env.OMAAX_TEST_COMPANY_BRIDGE)).href);
    const endpoint = `http://127.0.0.1:${server.address().port}/v1/chat/completions`;
    companyBridge = await startBridge({
      key:'fixture-only', session:async()=>({hub:'https://company-fixture.invalid',token:'fixture-session'}), endpoint,
      fetcher:async(url, options)=>{
        if(url==='https://company-fixture.invalid/api/llm/models') return Response.json({ok:true,models:[{id:'fixture-company',contextLength:1048576}]});
        if(url==='https://company-fixture.invalid/api/llm/token') return Response.json({ok:true,token:'fixture-proxy',expires_in:3600});
        assert.equal(url,endpoint,'test may only contact its local fixture');
        assert.ok(Buffer.byteLength(options.body)<=1048576,'oversized requests must never reach the company gateway');
        const body=JSON.parse(options.body);
        if(body.stream===true) { assert.equal(body.max_tokens,undefined); assert.equal(body.max_completion_tokens,undefined); }
        return fetch(url,options);
      },
    });
  }
  const servicePort = companyBridge?.port ?? server.address().port;
  const eventFile = path.join(home, 'test-events.jsonl'), observer = path.join(home, 'observer.mjs');
  const moduleFile = path.join(home, 'resolved-modules.json');
  // Resolve inside the real process: 0.2 installs its module resolution hooks
  // there rather than creating a node_modules link in each generated profile.
  writeFileSync(observer, `import {appendFileSync,writeFileSync} from 'node:fs'; import {createRequire} from 'node:module'; export function apply(ctx){ctx.on('session/event',(session,event)=>appendFileSync(${JSON.stringify(eventFile)},JSON.stringify({sessionId:session.id,event})+'\\n'),{global:true});try{const resolve=createRequire(${JSON.stringify(path.join(home,'profiles/web/cordis.yml'))}).resolve;writeFileSync(${JSON.stringify(moduleFile)},JSON.stringify({agent:resolve('@deepseek-ai/dsh-agent-loop'),compaction:resolve('@deepseek-ai/dsh-compaction-basic')}));}catch(error){writeFileSync(${JSON.stringify(moduleFile)},JSON.stringify({error:String(error)}));}}`);
  writeFileSync(path.join(home, 'cordis.patch.yml'), `- id: llm-deepseek\n  config:\n    baseURL: http://127.0.0.1:${servicePort}/v1\n    apiKeyEnv: OMAAX_TEST_KEY\n    maxTokens: 128000\n- insert:\n    - id: continuation-observer\n      name: ${JSON.stringify(pathToFileURL(observer).href)}\n`);
  if (JSON.parse(readFileSync(path.join(runtime, 'node_modules/@deepseek-ai/dsh/package.json'), 'utf8')).version.startsWith('0.2.')) {
    const config = path.join(home, 'cordis.patch.yml');
    const observerConfig = readFileSync(config, 'utf8').split('- insert:')[1];
    writeFileSync(config, `- id: llm-deepseek\n  disabled: true\n- id: llm-pi-ai\n  config:\n    providers:\n      deepseek-official:\n        api: openai-completions\n        baseURL: http://127.0.0.1:${servicePort}/v1\n        apiKeyEnv: OMAAX_TEST_KEY\n        models:\n          - id: fixture\n            contextWindow: 1048576\n            maxTokens: 128000\n            reasoningEfforts: false\n          - id: deepseek-flash\n            reasoningEfforts: {high: high}\n- id: agent-default-model\n  config:\n    provider: deepseek-official\n    model: fixture\n- insert:${observerConfig}`);
    writeFileSync(config,readFileSync(config,'utf8').replace('        models:', '        retryPolicy:\n          mode: normal\n          maxRetries: 2\n          backoff:\n            initialDelayMs: 250\n            maxDelayMs: 250\n            jitterRatio: 0\n        models:'));
  }
  const child = spawn(process.execPath, ['--expose-internals', path.join(runtime, 'node_modules/@deepseek-ai/dsh/lib/bin.js'), 'web', '--port', '0', '--no-open'], {cwd:workspace, windowsHide:true, env:{...process.env,DSH_HOME:home,DSH_TELEMETRY_DISABLED:'1',OMAAX_TEST_KEY:'fixture-only',DSH_MAX_STEPS:'0',DEEPSEEK_API_KEY:'',OPENAI_API_KEY:''}});
  let logs = '';
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('DSH startup timeout\n' + logs)), 30000);
      const scan = b => { logs += b; const m = logs.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/); if (m) {clearTimeout(timer); resolve(m[0]);} };
      child.stdout.on('data', scan); child.stderr.on('data', scan);
      child.once('exit', code => {clearTimeout(timer);reject(Error(`DSH exited ${code}: ${logs}`));});
    });
    const auth = await fetch(url, {redirect:'manual'});
    const cookie = auth.headers.getSetCookie().map(s => s.split(';')[0]).find(s => s.startsWith('dsh-auth-'));
    assert.ok(cookie);
    async function rpc(method, args) {
      const response = await fetch(`${new URL(url).origin}/api/${method}`, {method:'POST',headers:{'Content-Type':'application/json',Cookie:cookie},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),method,payload:{args}}),signal:AbortSignal.timeout(15000)});
      const result = (await response.json()).result;
      assert.equal(result?.ok, true, JSON.stringify(result)); return result.value;
    }
    const ws = await rpc('workspace/create', {request:{path:workspace}});
    await t.test('fresh profile resolves the same patched agent loop as the shipped entry', () => {
      const fromProfile = JSON.parse(readFileSync(moduleFile, 'utf8'));
      assert.ok(!fromProfile.error,fromProfile.error);
      const fromEntry = createRequire(path.join(runtime, 'node_modules/@deepseek-ai/dsh/lib/bin.js'));
      const profileAgent = realpathSync(fromProfile.agent);
      const shippedAgent = realpathSync(fromEntry.resolve('@deepseek-ai/dsh-agent-loop'));
      assert.equal(profileAgent, shippedAgent);
      assert.match(readFileSync(profileAgent, 'utf8'), /OMAAX_AUTO_CONTINUE_V1/);
      const profileCompaction=realpathSync(fromProfile.compaction);
      assert.equal(profileCompaction,realpathSync(fromEntry.resolve('@deepseek-ai/dsh-compaction-basic')));
      assert.match(readFileSync(profileCompaction,'utf8'),/OMAAX_COMPACTION_CONTINUE_V1/);
      if (process.env.OMAAX_TEST_DIAGNOSTICS) console.log('Fresh profile agent SHA256:', createHash('sha256').update(readFileSync(profileAgent)).digest('hex'));
    });
    async function run(mode, cancel = false) {
      const created = await rpc('session/create', {request:{workspaceId:ws.workspace?.workspaceId ?? ws.workspaceId}});
      const sessionId = created.sessionId ?? created.session?.sessionId ?? created.id;
      sessions.set(mode,sessionId);
      if(mode==='legacy') await rpc('session/selectModel',{request:{sessionId,provider:'deepseek-official',model:'deepseek-flash',reasoningEffort:'high'}});
      await rpc('session/prompt', {request:{sessionId,mode:'queue',requestId:randomUUID(),content:[{type:'text',text:`CASE:${mode} Complete fixture.`+(mode.startsWith('compact_')?' Preserve fixture detail and its original provenance. '.repeat(600):'')}]}});
      const deadline = Date.now() + 15000;
      if (cancel === 'retry') {
        while(Date.now()<deadline){const text=existsSync(eventFile)?readFileSync(eventFile,'utf8'):'';if(text.split('\n').filter(Boolean).map(JSON.parse).some(row=>row.sessionId===sessionId&&row.event.type==='llm/retry'))break;await sleep(20);}
        await rpc('session/cancel', {request:{sessionId}});
      } else if (cancel) {
        while (!pendingCancel && Date.now() < deadline) await sleep(25);
        assert.ok(pendingCancel, 'did not reach continuation request');
        await rpc('session/cancel', {request:{sessionId}});
      }
      let events;
      while (Date.now() < deadline) {
        events = existsSync(eventFile) ? readFileSync(eventFile,'utf8').trim().split('\n').filter(Boolean).map(s=>JSON.parse(s)).filter(row=>row.sessionId===sessionId).map(row=>row.event) : [];
        if (events.some(e => e.type === 'turn/end')) break;
        await sleep(40);
      }
      assert.ok(events.some(e => e.type === 'turn/end'), 'turn never finished: '+logs);
      if (process.env.OMAAX_TEST_DIAGNOSTICS) console.log(mode, JSON.stringify(events.find(e => e.type === 'turn/end')?.data));
      assert.equal(events.filter(e => e.type === 'turn/start').length, 1);
      assert.equal(events.filter(e => e.type === 'user/message' && e.data.source.kind === 'user').length, 1, 'synthetic continue is not a human prompt: '+JSON.stringify(events.filter(e=>e.type==='user/message')));
      return events;
    }
    await t.test('six successive length responses continue without a round quota', async () => {
      const events = await run('long');
      assert.equal(counts.get('long'), 7);
      assert.equal(events.find(e => e.type === 'turn/end').data.reason.kind, 'completed');
      assert.equal(events.filter(e => e.type === 'assistant/message').length, 7);
      assert.equal(events.filter(e => e.type === 'user/message' && (e.data.source.kind === 'omaax-output-continuation' || e.data.source.plugin === 'omaax-output-continuation')).length, 6);
      assert.equal(events.filter(e => e.type === 'assistant/message').reduce((n,e) => n + e.data.usage.totalTokens, 0), 168);
      const second = requests.get('long')[1]; assert.match(JSON.stringify(second.messages), /long segment 1/); assert.match(JSON.stringify(second.messages), /自动续接/);
    });
    await t.test('normal completion makes one call', async () => {await run('normal');assert.equal(counts.get('normal'),1);});
    await t.test('reasoning-only truncations continue until a visible answer is complete', async () => {
      const events = await run('reasoning');
      assert.equal(counts.get('reasoning'), 4);
      assert.equal(events.find(e => e.type === 'turn/end').data.reason.kind, 'completed');
      assert.equal(events.filter(e => e.type === 'user/message' && (e.data.source.kind === 'omaax-output-continuation' || e.data.source.plugin === 'omaax-output-continuation')).length, 3);
      assert.match(JSON.stringify(events.filter(e => e.type === 'assistant/message')), /Reasoning completed; final answer/);
    });
    await t.test('legacy model and reasoning selection still answer and auto-continue', async () => {const events=await run('legacy');assert.equal(counts.get('legacy'),2);assert.equal(events.find(e=>e.type==='turn/end').data.reason.kind,'completed');assert.equal(requests.get('legacy')[0].model,companyBridge?'fixture-company':'deepseek-flash');});
    await t.test('truncated tool arguments never execute; regenerated complete call executes once', async () => {
      const events = await run('tool');
      assert.equal(counts.get('tool'),3);
      assert.equal(events.filter(e => e.type === 'todo/write').length,1);
      assert.equal(events.find(e => e.type === 'turn/end').data.reason.kind,'completed');
      assert.ok(!JSON.stringify(requests.get('tool')[1].messages).includes('"id":"cut"'));
    });
    await t.test('stop during auto-continuation stops further requests', async () => {
      const events = await run('cancel',true); await sleep(100);
      assert.equal(counts.get('cancel'),2); assert.equal(events.find(e => e.type === 'turn/end').data.reason.kind,'aborted');
    });
    await t.test('an already completed tool is retained and never replayed by continuation', async () => {
      const events=await run('done_tool');assert.equal(counts.get('done_tool'),3);
      assert.equal(events.filter(e=>e.type==='todo/write').length,1);
      assert.equal(events.find(e=>e.type==='turn/end').data.reason.kind,'completed');
      assert.match(JSON.stringify(requests.get('done_tool')[2].messages),/Updated todo list/);
    });
    await t.test('oversized history is summarized in batches and the same task completes automatically', {skip:!companyBridge}, async()=>{
      const events=await run('oversized');
      assert.equal(events.find(e=>e.type==='turn/end').data.reason.kind,'completed');
      assert.equal(events.filter(e=>e.type==='todo/write').length,1,'completed tools cannot be executed twice');
      assert.equal(counts.get('oversized'),3,'transport rejection must not reach the cloud');
      assert.ok(counts.get('oversized_summary')>=4,'large checkpoint uses multiple small requests');
      assert.equal(events.filter(e=>e.type==='compaction/end'&&!e.data.error).length,1);
      assert.ok(events.some(e=>e.type==='assistant/message'&&Buffer.byteLength(JSON.stringify(e.data.message))>1048576),'original large message remains in durable history');
      assert.match(JSON.stringify(requests.get('oversized').at(-1).messages),/running-175/);
      assert.ok(Buffer.byteLength(JSON.stringify(requests.get('oversized').at(-1)))<1048576);
    });
    await t.test('empty and identical provider responses do not loop forever', async () => {
      for (const mode of ['empty','repeated']) {const events=await run(mode);assert.equal(events.find(e=>e.type==='turn/end').data.reason.error.code,'AUTO_CONTINUE_STALLED');}
      assert.equal(counts.get('empty'),2);assert.equal(counts.get('repeated'),3);
    });
    await t.test('HTTP rejection does not cause a continuation or retry', async () => {
      const events=await run('error');assert.equal(counts.get('error'),1);assert.equal(events.find(e=>e.type==='turn/end').data.reason.kind,'error');
    });
    await t.test('temporary company error recovers with a single bounded retry', {skip:!companyBridge}, async()=>{
      const events=await run('retry_ok');assert.equal(counts.get('retry_ok'),2);assert.equal(events.filter(e=>e.type==='llm/retry').length,1);assert.equal(events.find(e=>e.type==='turn/end').data.reason.kind,'completed');
    });
    await t.test('retry cap and genuine access denial remain enforced', {skip:!companyBridge}, async()=>{
      const limited=await run('retry_exhausted');assert.equal(counts.get('retry_exhausted'),3);assert.equal(limited.filter(e=>e.type==='llm/retry').length,2);assert.equal(limited.find(e=>e.type==='turn/end').data.reason.error.code,'model_service_unavailable');
      const denied=await run('forbidden');assert.equal(counts.get('forbidden'),1);assert.equal(denied.filter(e=>e.type==='llm/retry').length,0);assert.equal(denied.find(e=>e.type==='turn/end').data.reason.error.code,'model_access_denied');
    });
    await t.test('user stop interrupts the pending retry without another provider request', {skip:!companyBridge}, async()=>{
      const events=await run('retry_cancel','retry');await sleep(300);assert.equal(counts.get('retry_cancel'),1);assert.equal(events.find(e=>e.type==='turn/end').data.reason.kind,'aborted');assert.equal(events.filter(e=>e.type==='llm/retry').length,1);
    });
    const history = sessionId => readFileSync(eventFile,'utf8').trim().split('\n').map(JSON.parse).filter(row=>row.sessionId===sessionId).map(row=>row.event);
    await t.test('truncated checkpoint continues, commits once, keeps history and allows the next turn', async()=>{
      const original = await run('compact_ok'), sessionId=sessions.get('compact_ok');
      await rpc('commands/execute',{agentId:sessionId,line:'/compact',submittedAttachments:[]});
      const events=history(sessionId);
      assert.equal(counts.get('compact_ok_summary'),3);
      const summaries=events.filter(e=>e.type==='compaction/summary');
      assert.equal(summaries.length,1);
      assert.match(JSON.stringify(summaries[0].data.summary),/Primary Request/);
      assert.match(JSON.stringify(summaries[0].data.summary),/Next Steps/);
      assert.equal(summaries[0].data.usage.totalTokens,72);
      assert.equal(events.filter(e=>e.type==='compaction/end'&&!e.data.error).length,1);
      assert.equal(events.filter(e=>e.type==='user/message'&&e.surfaceOp?.op==='replace').length,1);
      for(const event of original) assert.ok(events.some(e=>e.seq===event.seq&&JSON.stringify(e)===JSON.stringify(event)),'original history must remain durable');
      await rpc('session/prompt',{request:{sessionId,mode:'queue',requestId:randomUUID(),content:[{type:'text',text:'CASE:normal Continue after compaction.'}]}});
      const deadline=Date.now()+10000;
      while(history(sessionId).filter(e=>e.type==='turn/end').length<2&&Date.now()<deadline) await sleep(30);
      assert.equal(history(sessionId).filter(e=>e.type==='turn/end').at(-1).data.reason.kind,'completed');
      assert.equal(history(sessionId).filter(e=>e.type==='turn/end').length,2);
    });
    await t.test('failed or cancelled checkpoint never replaces the original conversation',async()=>{
      for(const mode of ['compact_empty','compact_cancel']) {
        await run(mode); const sessionId=sessions.get(mode);
        const command=rpc('commands/execute',{agentId:sessionId,line:'/compact',submittedAttachments:[]});
        if(mode==='compact_cancel') {
          const deadline=Date.now()+10000;
          while(!pendingCompact&&Date.now()<deadline)await sleep(25);
          assert.ok(pendingCompact,'checkpoint continuation did not start');
          await rpc('session/cancel',{request:{sessionId}});
        }
        await command;
        const events=history(sessionId);
        assert.equal(events.filter(e=>e.type==='compaction/summary').length,0);
        assert.equal(events.filter(e=>e.surfaceOp?.op==='replace').length,0);
        assert.equal(events.filter(e=>e.type==='compaction/end'&&e.data.error).length,1);
        assert.ok(events.some(e=>e.type==='user/message'&&JSON.stringify(e).includes('Preserve fixture detail')));
      }
    });
  } finally {
    if (process.env.OMAAX_TEST_DIAGNOSTICS) {
      const evidence=path.join(desktop,'../validation-local/continuation-debug.json');
      mkdirSync(path.dirname(evidence),{recursive:true});
      writeFileSync(evidence,JSON.stringify({logs,requests:Object.fromEntries(requests),events:existsSync(eventFile)?readFileSync(eventFile,'utf8'):''}));
    }
    pendingCancel?.destroy();
    pendingCompact?.destroy();
    if(child.exitCode===null){const stopped=once(child,'exit');child.kill();await stopped;}
    await companyBridge?.close();
    server.closeAllConnections(); await new Promise(resolve=>server.close(resolve));
    const temporaryRoot=path.resolve(tmpdir()), resolvedHome=path.resolve(home);
    assert.ok(resolvedHome.startsWith(temporaryRoot+path.sep) && path.basename(resolvedHome).startsWith('omaax-continue-'));
    rmSync(resolvedHome,{recursive:true,force:true});
  }
});
