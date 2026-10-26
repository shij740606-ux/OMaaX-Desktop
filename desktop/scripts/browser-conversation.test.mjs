// Real stable DSH conversations and real Edge; a loopback model supplies deterministic tools.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn, execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const desktop = fileURLToPath(new URL('..', import.meta.url));
const entry = process.env.OMAAX_BROWSER_TEST_ENTRY || path.join(desktop, 'resources/browser/index.mjs');
const modules = process.env.OMAAX_BROWSER_TEST_DSH_MODULES || path.join(desktop, 'dsh-runtime/node_modules/@deepseek-ai/dsh/node_modules');
const dsh = path.dirname(modules);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

test('main conversation discovers browser tools, operates a page and continues across turns', { timeout: 180000 }, async () => {
  assert.equal(JSON.parse(readFileSync(path.join(dsh, 'package.json'))).version, '0.1.5-rc.1');
  const run = path.resolve(desktop, `../validation-local/browser-conversation-${Date.now()}`);
  const home = path.join(run, 'dsh-home'), workspace = path.join(run, '中文 项目');
  mkdirSync(home, { recursive: true }); mkdirSync(workspace, { recursive: true });
  const env = { ...process.env, DSH_HOME: home, DSH_TELEMETRY_DISABLED: '1', OMAAX_TEST_KEY: 'fixture-only',
    DEEPSEEK_API_KEY: '', OPENAI_API_KEY: '', ANTHROPIC_API_KEY: '', NODE_OPTIONS: '',
    OMAAX_DSH_MODULES: modules, OMAAX_BROWSER_HOME: path.join(run, 'browser-audit') };
  const counters = { first: 0, second: 0 }, requests = [];
  let site;
  const operations = mode => mode === 'first' ? [
    { name: 'omaax_browser', args: { action: 'start' } },
    { name: 'mcp__omaax_browser__browser_navigate', args: { url: site } },
    { name: 'mcp__omaax_browser__browser_type', args: { target: 'input[aria-label="Topic"]', text: 'Rice genomics' } },
    { name: 'mcp__omaax_browser__browser_click', args: { target: 'button' } },
    { name: 'mcp__omaax_browser__browser_take_screenshot', args: { type: 'png', fullPage: true, scale: 'css' } },
  ] : [
    { name: 'mcp__omaax_browser__browser_snapshot', args: {} },
    { name: 'omaax_browser', args: { action: 'close' } },
  ];
  const server = createServer(async (req, res) => {
    if (req.method === 'GET' && req.url.includes('/files/')) {
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ id: 'file-browser-fixture', object: 'file', purpose: 'user_data', filename: 'browser.png', bytes: 100, created_at: 1, status: 'processed' })); return;
    }
    if (req.method === 'GET') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end('<!doctype html><title>Research portal</title><h1>Project page</h1><label>Topic <input aria-label="Topic"></label><button onclick="document.querySelector(\'output\').textContent=document.querySelector(\'input\').value">Save topic</button><output>No topic yet</output>'); return;
    }
    let raw = ''; for await (const part of req) raw += part;
    if (String(req.headers['content-type']).includes('multipart/form-data')) {
      res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ id: 'file-browser-fixture', object: 'file', purpose: 'user_data', filename: 'browser.png', bytes: raw.length, created_at: 1, status: 'processed' })); return;
    }
    const body = JSON.parse(raw);
    if (!body.stream) { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: 'Browser test' }, finish_reason: 'stop' }] })); return; }
    const mode = JSON.stringify(body.messages).includes('CONTINUE_BROWSER') ? 'second' : 'first';
    const active = JSON.stringify(body.messages).includes('OPEN_BROWSER') && body.tools?.length;
    const op = active ? operations(mode)[counters[mode]++] : undefined;
    if (active) requests.push(body);
    const delta = op ? { tool_calls: [{ index: 0, id: randomUUID(), type: 'function', function: { name: op.name, arguments: JSON.stringify(op.args) } }] } : { content: 'The browser task is complete and the screenshot is saved in the project.' };
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end(`data: ${JSON.stringify({ choices: [{ index: 0, delta, finish_reason: null }] })}\n\ndata: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: op ? 'tool_calls' : 'stop' }], usage: { prompt_tokens: 30, completion_tokens: 10, total_tokens: 40 } })}\n\ndata: [DONE]\n\n`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  site = `http://127.0.0.1:${server.address().port}/site`;
  const eventsFile = path.join(run, 'events.jsonl'), observer = path.join(run, 'observer.mjs');
  writeFileSync(observer, `import {appendFileSync} from 'node:fs'; export function apply(ctx){ctx.on('session/event',(session,event)=>appendFileSync(${JSON.stringify(eventsFile)},JSON.stringify({sessionId:session.id,event})+String.fromCharCode(10)),{global:true});}`);
  writeFileSync(path.join(home, 'cordis.patch.yml'), `- id: llm-deepseek\n  config:\n    baseURL: http://127.0.0.1:${server.address().port}/v1\n    apiKeyEnv: OMAAX_TEST_KEY\n- insert:\n    - id: omaax-browser\n      name: ${JSON.stringify(pathToFileURL(entry).href)}\n      config:\n        headless: true\n    - id: browser-observer\n      name: ${JSON.stringify(pathToFileURL(observer).href)}\n`);
  const child = spawn(process.execPath, ['--expose-internals', path.join(dsh, 'lib/bin.js'), 'web', '--port', '0', '--no-open'], { cwd: workspace, env, windowsHide: true });
  let logs = '';
  child.stdout.on('data', data => logs += data); child.stderr.on('data', data => logs += data);
  try {
    const deadline = Date.now() + 40000;
    while (!logs.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/) && Date.now() < deadline && child.exitCode === null) await pause(50);
    const url = logs.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/)?.[0];
    assert.ok(url, `Runtime startup failed: ${logs}`);
    const auth = await fetch(url, { redirect: 'manual' });
    const cookie = auth.headers.getSetCookie().map(value => value.split(';')[0]).find(value => value.startsWith('dsh-auth-'));
    const rpc = async (method, args) => {
      const response = await fetch(`${new URL(url).origin}/api/${method}`, { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: cookie }, body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method, payload: { args } }), signal: AbortSignal.timeout(15000) });
      const result = (await response.json()).result; assert.equal(result?.ok, true, JSON.stringify(result)); return result.value;
    };
    const ws = await rpc('workspace/create', { request: { path: workspace } });
    const session = await rpc('session/create', { request: { workspaceId: ws.workspace?.workspaceId ?? ws.workspaceId } });
    const sessionId = session.sessionId ?? session.session?.sessionId ?? session.id;
    const history = () => existsSync(eventsFile) ? readFileSync(eventsFile, 'utf8').split('\n').slice(0, -1).filter(Boolean).map(JSON.parse).filter(row => row.sessionId === sessionId).map(row => row.event) : [];
    for (const [index, prompt] of ['OPEN_BROWSER 打开研究网页，填写主题并截图保存在项目。', 'CONTINUE_BROWSER 查看上一轮网页内容，然后关闭浏览器。'].entries()) {
      await rpc('session/prompt', { request: { sessionId, mode: 'queue', requestId: randomUUID(), content: [{ type: 'text', text: prompt }] } });
      const end = Date.now() + 60000;
      while (history().filter(event => event.type === 'turn/end').length <= index && Date.now() < end) await pause(100);
      const events = history(), turnEnds = events.filter(event => event.type === 'turn/end');
      assert.equal(turnEnds.length, index + 1, 'Browser conversation did not finish');
      assert.equal(turnEnds.at(-1).data.reason.kind, 'completed');
      const failures = events.filter(event => event.type === 'tool/result').flatMap(event => event.data.message.content).filter(result => result.isError);
      assert.deepEqual(failures, [], 'A browser call failed');
    }
    assert.ok(requests[0].tools.some(tool => tool.function.name === 'omaax_browser'));
    assert.ok(!requests[0].tools.some(tool => tool.function.name === 'mcp__omaax_browser__browser_navigate'));
    assert.ok(requests[1].tools.some(tool => tool.function.name === 'mcp__omaax_browser__browser_navigate'), 'Discovered tools must reach the next model request');
    assert.ok(requests.some(body => JSON.stringify(body.messages).includes('Rice genomics')));
    const screenshots = readdirSync(path.join(workspace, 'browser-results'), { recursive: true }).filter(name => name.endsWith('.png'));
    assert.equal(screenshots.length, 1);
    writeFileSync(path.join(run, 'result.json'), JSON.stringify({ passed: true, stableRuntime: '0.1.5-rc.1', turns: 2, modelToolCalls: 7, liveCloudCalls: 0, browser: 'system Edge', protected: entry.includes('protected'), screenshot: path.join(workspace, 'browser-results', screenshots[0]) }, null, 2));
    console.log('Browser conversation evidence:', run);
  } catch (error) { console.error(error); throw error; } finally {
    writeFileSync(path.join(run, 'runtime.log'), logs.replaceAll(/\?token=[A-Za-z0-9._-]+/g, '?token=[local-test]'));
    if (process.platform === 'win32' && child.exitCode === null) { try { execFileSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }); } catch {} }
    else child.kill();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    child.stdout.destroy(); child.stderr.destroy(); child.unref();
  }
});
