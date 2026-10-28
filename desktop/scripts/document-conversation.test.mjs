// Real bundled DSH + real document engines; a local scripted model avoids cloud
// credentials and makes the two-turn file creation/editing contract repeatable.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn, execFileSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const desktop = fileURLToPath(new URL('..', import.meta.url));
const dsh = path.join(process.env.OMAAX_TEST_DSH_RUNTIME || path.join(desktop, 'dsh-runtime'), 'node_modules/@deepseek-ai/dsh');
const evidence = path.resolve(desktop, '../validation-local/documents');
const documentResources = path.resolve(process.env.OMAAX_DOCUMENT_TEST_RESOURCES || path.join(desktop, 'resources/documents'));
const cli = path.join(documentResources, 'cli.mjs');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const quote = value => "'" + value.replaceAll("'", "''") + "'";
const hash = file => createHash('sha256').update(readFileSync(file)).digest('hex');

test('bundled runtime conversation creates and revises project Word/PPT/PDF files', { timeout: 300000 }, async () => {
  assert.equal(JSON.parse(readFileSync(path.join(dsh, 'package.json'))).version, '0.2.1-rc.2');
  const run = path.join(evidence, `conversation-${Date.now()}`);
  const home = path.join(run, 'dsh-home');
  const workspace = path.join(run, '中文 项目');
  mkdirSync(home, { recursive: true });
  mkdirSync(workspace, { recursive: true });
  const env = { ...process.env, OMAAX_DOCUMENT_HOME: process.env.OMAAX_DOCUMENT_TEST_HOME || path.join(evidence, 'runtime'),
    OMAAX_DOCUMENT_UV: process.env.OMAAX_DOCUMENT_TEST_UV || path.join(desktop, 'resources/bin/win32-x64/uv.exe'),
    OMAAX_DOCUMENT_NODE: process.execPath, OMAAX_DOCUMENT_RUNNER: cli,
    DSH_HOME: home, DSH_AGENTS_HOME: path.join(run, 'agents-home'), DSH_TELEMETRY_DISABLED: '1', OMAAX_TEST_KEY: 'fixture-only',
    DEEPSEEK_API_KEY: '', OPENAI_API_KEY: '', ANTHROPIC_API_KEY: '', NODE_OPTIONS: '',
    DSH_BUNDLED_SKILL_DIR: path.join(documentResources, 'skills') };
  const pythonScript = path.join(workspace, 'generate_documents.py');
  copyFileSync(path.join(desktop, 'qa/documents/document_fixture.py'), pythonScript);
  // First-use preparation uses the same shipped CLI, with an isolated cache.
  const prepared = JSON.parse(execFileSync(process.execPath, [cli, 'prepare'], { env, cwd: workspace, windowsHide: true, timeout: 180000, encoding: 'utf8' }));
  const shell = args => ({ name: 'pwsh', args: {
    command: `& ${quote(prepared.python)} -E -s -B -X utf8 -u ${args.map(quote).join(' ')}`,
    description: 'Generate project document fixture', timeoutMs: 60000,
  } });
  const docs = path.join(workspace, 'documents');
  const operations = mode => {
    const version = mode === 'create' ? 'v1' : 'v2';
    return [
      ...(mode === 'create' ? [{ name: 'skill', args: { name: 'omaax-documents' } }, { name: 'omaax_documents', args: { action: 'prepare' } }] : []),
      shell([pythonScript, mode]),
      ...['report', 'slides'].map(name => ({ name: 'omaax_documents', args: { action: 'pdf', input: path.join(docs, `${name}-${version}.${name === 'report' ? 'docx' : 'pptx'}`), output: path.join(docs, `${name}-${version}.pdf`) } })),
      { name: 'present', args: { files: [
        { path: path.join(docs, `report-${version}.docx`), description: 'Word report' },
        { path: path.join(docs, `slides-${version}.pptx`), description: 'Editable presentation' },
        { path: path.join(docs, `report-${version}.pdf`), description: 'Report PDF' },
        { path: path.join(docs, `slides-${version}.pdf`), description: 'Presentation PDF' },
      ] } },
    ];
  };
  const counters = { create: 0, edit: 0 };
  const requests = [];
  const server = createServer(async (req, res) => {
    let raw = ''; for await (const part of req) raw += part;
    const body = JSON.parse(raw);
    if (!body.stream) { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: 'Document fixture' }, finish_reason: 'stop' }] })); return; }
    const mode = JSON.stringify(body.messages).includes('EDIT_DOCUMENTS') ? 'edit' : 'create';
    const active = JSON.stringify(body.messages).includes('CREATE_DOCUMENTS') && body.tools?.length;
    const sequence = operations(mode);
    const operation = active ? sequence[counters[mode]++] : undefined;
    if (active) requests.push(body);
    const delta = operation ? { tool_calls: [{ index: 0, id: randomUUID(), type: 'function', function: { name: operation.name, arguments: JSON.stringify(operation.args) } }] } : { content: 'Documents saved to the project.' };
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end(`data: ${JSON.stringify({ choices: [{ index: 0, delta, finish_reason: null }] })}\n\ndata: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: operation ? 'tool_calls' : 'stop' }], usage: { prompt_tokens: 30, completion_tokens: 10, total_tokens: 40 } })}\n\ndata: [DONE]\n\n`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const eventsFile = path.join(run, 'events.jsonl');
  const observer = path.join(run, 'observer.mjs');
  writeFileSync(observer, `import {appendFileSync} from 'node:fs'; export function apply(ctx){ctx.on('session/event',(session,event)=>appendFileSync(${JSON.stringify(eventsFile)},JSON.stringify({sessionId:session.id,event})+'\\n'),{global:true});}`);
  writeFileSync(path.join(home, 'cordis.patch.yml'), `- id: llm-deepseek\n  disabled: true\n- id: llm-pi-ai\n  config:\n    providers:\n      deepseek-official:\n        api: openai-completions\n        baseURL: http://127.0.0.1:${server.address().port}/v1\n        apiKeyEnv: OMAAX_TEST_KEY\n        models:\n          - id: deepseek-flash\n            name: Local fixture\n- insert:\n    - id: omaax-documents\n      name: ${JSON.stringify(pathToFileURL(path.join(documentResources, 'index.mjs')).href)}\n    - id: documents-observer\n      name: ${JSON.stringify(pathToFileURL(observer).href)}\n`);
  const child = spawn(process.execPath, ['--expose-internals', path.join(dsh, 'lib/bin.js'), 'web', '--port', '0', '--no-open'], { cwd: workspace, env, windowsHide: true });
  let logs = '';
  const log = data => { logs += data.toString(); };
  child.stdout.on('data', log); child.stderr.on('data', log);
  try {
    const startupDeadline = Date.now() + 40000;
    while (!logs.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/) && Date.now() < startupDeadline && child.exitCode === null) await pause(50);
    const url = logs.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/)?.[0];
    assert.ok(url, `DSH startup failed: ${logs}`);
    const auth = await fetch(url, { redirect: 'manual' });
    const cookie = auth.headers.getSetCookie().map(s => s.split(';')[0]).find(s => s.startsWith('dsh-auth-'));
    const rpc = async (method, args) => {
      const response = await fetch(`${new URL(url).origin}/api/${method}`, { method: 'POST', headers: { 'Content-Type': 'application/json', Cookie: cookie }, body: JSON.stringify({ type: 'client-request', rpcId: randomUUID(), method, payload: { args } }), signal: AbortSignal.timeout(15000) });
      const result = (await response.json()).result;
      assert.equal(result?.ok, true, JSON.stringify(result)); return result.value;
    };
    const ws = await rpc('workspace/create', { request: { path: workspace } });
    const session = await rpc('session/create', { request: { workspaceId: ws.workspace?.workspaceId ?? ws.workspaceId } });
    const sessionId = session.sessionId ?? session.session?.sessionId ?? session.id;
    const history = () => existsSync(eventsFile) ? readFileSync(eventsFile, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse).filter(row => row.sessionId === sessionId).map(row => row.event) : [];
    const prompt = async (text, turn) => {
      await rpc('session/prompt', { request: { sessionId, mode: 'queue', requestId: randomUUID(), content: [{ type: 'text', text }] } });
      const deadline = Date.now() + 100000;
      while (history().filter(e => e.type === 'turn/end').length < turn && Date.now() < deadline) await pause(100);
      const events = history();
      assert.equal(events.filter(e => e.type === 'turn/end').length, turn, 'Conversation did not finish');
      assert.equal(events.filter(e => e.type === 'turn/end').at(-1).data.reason.kind, 'completed', JSON.stringify(events.at(-1)));
      const failures = events.filter(e => e.type === 'tool/result' && e.data.turn === turn).map(e => e.data.message).filter(message => message.isError || message.content.some(result => result.isError || /\[exit code: [1-9]/.test(JSON.stringify(result))));
      assert.deepEqual(failures, [], 'Document tools must succeed before reporting completion');
      return events;
    };
    let events = await prompt('CREATE_DOCUMENTS 把这些结果整理成 Word、PPT 和 PDF，保存到当前项目。', 1);
    assert.ok(events.some(e => e.type === 'user/message' && e.data.source.kind === 'skill-catalog' && e.data.source.entries.some(entry => entry.name === 'omaax-documents')), 'Default document skill must be in the model catalog');
    assert.ok(requests[0].tools.some(tool => tool.function.name === 'omaax_documents'), 'Fixed document tool must be callable in the main conversation');
    for (const ext of ['docx', 'pdf']) assert.ok(existsSync(path.join(docs, `report-v1.${ext}`)), 'Model tools must create real report files');
    const originalDoc = hash(path.join(docs, 'report-v1.docx'));
    const originalDeck = hash(path.join(docs, 'slides-v1.pptx'));
    events = await prompt('EDIT_DOCUMENTS 修改上一轮的标题，保留图片表格和数值，另存第二版。', 2);
    assert.equal(hash(path.join(docs, 'report-v1.docx')), originalDoc, 'Original report preserved');
    assert.equal(hash(path.join(docs, 'slides-v1.pptx')), originalDeck, 'Original presentation preserved');
    assert.equal(events.filter(e => e.type === 'deliverables/presented').length, 2, 'Each turn presents real project files');
    assert.equal(events.filter(e => e.type === 'user/message' && e.data.source.kind === 'user').length, 2);
    const verification = execFileSync(process.execPath, [cli, 'run', pythonScript, 'verify'], { env, cwd: workspace, windowsHide: true, encoding: 'utf8', timeout: 30000 });
    assert.ok(events.filter(e => e.type === 'sandbox/mode').every(e => e.data.mode === 'workspace-write'), 'Document creation must not widen the conversation sandbox');
    writeFileSync(path.join(run, 'result.json'), JSON.stringify({ passed: true, runtime: '0.2.1-rc.2', turns: 2, liveCloudCalls: 0, sandbox: 'workspace-write', verification: verification.trim(), project: workspace }, null, 2));
    console.log('Document conversation evidence:', run);
  } finally {
    writeFileSync(path.join(run, 'runtime.log'), logs.replaceAll(/\?token=[A-Za-z0-9._-]+/g, '?token=[local-test]'));
    child.kill();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
});
