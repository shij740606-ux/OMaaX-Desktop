// Exercise the shipped DSH agent, HTTP adapter and durable sessions, against a
// synthetic local SSE server. No live credentials, data, or model charges.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { patchContinuation } from './patch-dsh-continuation.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runtime = process.env.OMAAX_TEST_DSH_RUNTIME ? path.resolve(process.env.OMAAX_TEST_DSH_RUNTIME) : path.join(desktop, 'dsh-runtime');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const sse = (delta, finish = 'stop') => 'data: ' + JSON.stringify({id:'fixture',choices:[{index:0,delta,finish_reason:null}]}) + '\n\ndata: ' + JSON.stringify({choices:[{index:0,delta:{},finish_reason:finish}],usage:{prompt_tokens:20,completion_tokens:4,total_tokens:24}}) + '\n\ndata: [DONE]\n\n';

test('continuation patch is idempotent', () => {
  const files = patchContinuation(runtime);
  const before = files.map(f => readFileSync(f, 'utf8'));
  patchContinuation(runtime);
  assert.deepEqual(files.map(f => readFileSync(f, 'utf8')), before);
});

test('real DSH automatically continues output limits, preserves history and honors stop/errors', {timeout:120000}, async t => {
  const home = mkdtempSync(path.join(tmpdir(), 'omaax-continue-'));
  const workspace = path.join(home, 'workspace'); mkdirSync(workspace);
  const counts = new Map(), requests = new Map(); let pendingCancel;
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const part of req) raw += part;
    const body = JSON.parse(raw);
    const match = JSON.stringify(body.messages).match(/CASE:(\w+)/);
    if (!match || !body.tools?.length) { res.writeHead(200, {'Content-Type':'text/event-stream'}); res.end(sse({content:'Fixture title'})); return; }
    const mode = match[1], count = (counts.get(mode) ?? 0) + 1;
    counts.set(mode, count); requests.set(mode, [...requests.get(mode) ?? [], body]);
    if (mode === 'error') { res.writeHead(400, {'Content-Type':'application/json'}); res.end(JSON.stringify({error:{message:'fixture rejection'}})); return; }
    res.writeHead(200, {'Content-Type':'text/event-stream'});
    if (mode === 'cancel' && count === 2) { res.write('data: '+JSON.stringify({choices:[{index:0,delta:{content:'in flight'},finish_reason:null}]})+'\n\n'); pendingCancel = res; return; }
    if (mode === 'empty') { res.end(sse({}, 'length')); return; }
    if (mode === 'repeated') { res.end(sse({content:'Identical response'}, 'length')); return; }
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
    if (mode === 'normal') { res.end(sse({content:'Normal answer'})); return; }
    const limit = mode === 'long' ? 6 : 1;
    res.end(sse({content:`${mode} segment ${count}`}, count <= limit ? 'length' : 'stop'));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const eventFile = path.join(home, 'test-events.jsonl'), observer = path.join(home, 'observer.mjs');
  writeFileSync(observer, `import {appendFileSync} from 'node:fs'; export function apply(ctx){ctx.on('session/event',(session,event)=>appendFileSync(${JSON.stringify(eventFile)},JSON.stringify({sessionId:session.id,event})+'\\n'),{global:true});}`);
  writeFileSync(path.join(home, 'cordis.patch.yml'), `- id: llm-deepseek\n  config:\n    baseURL: http://127.0.0.1:${server.address().port}/v1\n    apiKeyEnv: OMAAX_TEST_KEY\n    maxTokens: 128000\n- insert:\n    - id: continuation-observer\n      name: ${JSON.stringify(pathToFileURL(observer).href)}\n`);
  if (JSON.parse(readFileSync(path.join(runtime, 'node_modules/@deepseek-ai/dsh/package.json'), 'utf8')).version.startsWith('0.2.')) {
    const config = path.join(home, 'cordis.patch.yml');
    const observerConfig = readFileSync(config, 'utf8').split('- insert:')[1];
    writeFileSync(config, `- id: llm-deepseek\n  disabled: true\n- id: llm-pi-ai\n  config:\n    providers:\n      deepseek-official:\n        api: openai-completions\n        baseURL: http://127.0.0.1:${server.address().port}/v1\n        apiKeyEnv: OMAAX_TEST_KEY\n        models:\n          - id: fixture\n            contextWindow: 1048576\n            maxTokens: 128000\n            reasoningEfforts: false\n          - id: deepseek-flash\n            reasoningEfforts: {high: high}\n- id: agent-default-model\n  config:\n    provider: deepseek-official\n    model: fixture\n- insert:${observerConfig}`);
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
    async function run(mode, cancel = false) {
      const created = await rpc('session/create', {request:{workspaceId:ws.workspace?.workspaceId ?? ws.workspaceId}});
      const sessionId = created.sessionId ?? created.session?.sessionId ?? created.id;
      if(mode==='legacy') await rpc('session/selectModel',{request:{sessionId,provider:'deepseek-official',model:'deepseek-flash',reasoningEffort:'high'}});
      await rpc('session/prompt', {request:{sessionId,mode:'queue',requestId:randomUUID(),content:[{type:'text',text:`CASE:${mode} Complete fixture.`}]}});
      const deadline = Date.now() + 15000;
      if (cancel) {
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
      assert.equal(events.filter(e => e.type === 'user/message' && e.data.source.kind === 'user').length, 1, 'synthetic continue is not a human prompt');
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
    await t.test('legacy model and reasoning selection still answer and auto-continue', async () => {const events=await run('legacy');assert.equal(counts.get('legacy'),2);assert.equal(events.find(e=>e.type==='turn/end').data.reason.kind,'completed');assert.equal(requests.get('legacy')[0].model,'deepseek-flash');});
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
    await t.test('empty and identical provider responses do not loop forever', async () => {
      for (const mode of ['empty','repeated']) {const events=await run(mode);assert.equal(events.find(e=>e.type==='turn/end').data.reason.error.code,'AUTO_CONTINUE_STALLED');}
      assert.equal(counts.get('empty'),2);assert.equal(counts.get('repeated'),3);
    });
    await t.test('HTTP rejection does not cause a continuation or retry', async () => {
      const events=await run('error');assert.equal(counts.get('error'),1);assert.equal(events.find(e=>e.type==='turn/end').data.reason.kind,'error');
    });
  } finally {
    pendingCancel?.destroy();
    if(child.exitCode===null){const stopped=once(child,'exit');child.kill();await stopped;}
    server.closeAllConnections(); await new Promise(resolve=>server.close(resolve));
    const temporaryRoot=path.resolve(tmpdir()), resolvedHome=path.resolve(home);
    assert.ok(resolvedHome.startsWith(temporaryRoot+path.sep) && path.basename(resolvedHome).startsWith('omaax-continue-'));
    rmSync(resolvedHome,{recursive:true,force:true});
  }
});
