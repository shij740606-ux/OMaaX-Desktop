import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, utimes, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { spawn, spawnSync } from 'node:child_process';
import { acquireLock, convert, documentConversionError, isolatedPythonEnv, parseArguments, runProcess, runtimeHome } from '../resources/documents/cli.mjs';

test('strict commands preserve script arguments and validate document options', () => {
  assert.deepEqual(parseArguments(['prepare']), { command: 'prepare' });
  const run = parseArguments(['run', 'script.py', 'a b', '--title', '研究汇报']);
  assert.equal(path.basename(run.script), 'script.py');
  assert.deepEqual(run.args, ['a b', '--title', '研究汇报']);
  const preview = parseArguments(['preview', '--input', '报告.pptx', '--output-dir', '预览', '--pages', '2,1,2', '--dpi', '200']);
  assert.deepEqual(preview.pages, [2, 1]);
  assert.equal(preview.dpi, 200);
  for (const args of [[], ['prepare', 'extra'], ['run', 'code.sh'], ['pdf', '--input', 'x.pdf', '--output', 'x.pdf'], ['pdf', '--input', 'x.docx', '--output', 'x.txt'], ['pdf', '--input', 'x.docx', '--input', 'y.docx', '--output', 'x.pdf'], ['preview', '--input', 'x.docx', '--output-dir', 'x', '--pages', '0'], ['preview', '--input', 'x.docx', '--output-dir', 'x', '--dpi', '900']]) assert.throws(() => parseArguments(args));
});

test('document environment is explicit and cannot adopt analysis paths or proxies', () => {
  assert.throws(() => runtimeHome({}));
  assert.throws(() => runtimeHome({ OMAAX_DOCUMENT_HOME: path.parse(process.cwd()).root }));
  const home = path.resolve('document-home');
  const env = isolatedPythonEnv(home, { PATH: 'original', VIRTUAL_ENV: 'bioenv', PYTHONPATH: 'education', UV_PROJECT_ENVIRONMENT: 'analysis', UV_INDEX_URL: 'private', HTTPS_PROXY: 'proxy', http_proxy: 'proxy', ALL_PROXY: 'proxy' });
  assert.equal(env.PATH, 'original');
  assert.equal(env.UV_CACHE_DIR, path.join(home, 'cache'));
  assert.equal(env.UV_LINK_MODE, 'copy');
  for (const key of ['VIRTUAL_ENV', 'PYTHONPATH', 'UV_PROJECT_ENVIRONMENT', 'UV_INDEX_URL', 'HTTPS_PROXY', 'http_proxy', 'ALL_PROXY']) assert.equal(env[key], undefined);
});

test('concurrent prepares serialize and cancelled waiters do not unlock another task', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'omaax-document-lock-'));
  const file = path.join(dir, '.prepare.lock');
  try {
    const owner = await acquireLock(file);
    const controller = new AbortController();
    const waiting = acquireLock(file, { signal: controller.signal, pollMs: 10 });
    controller.abort(new Error('cancelled by user'));
    await assert.rejects(waiting, /cancelled|aborted/);
    assert.equal(JSON.parse(await readFile(file)).pid, process.pid);
    await assert.rejects(acquireLock(file, { timeoutMs: 20, pollMs: 10 }), /another task/);
    const next = acquireLock(file, { pollMs: 10 });
    await owner.release();
    const second = await next;
    await owner.release();
    assert.equal(JSON.parse(await readFile(file)).pid, process.pid);
    await second.release();
    await assert.rejects(readFile(file), { code: 'ENOENT' });
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('stale setup lock recovers but a surviving child prevents concurrent mutation', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'omaax-document-stale-'));
  const file = path.join(dir, '.prepare.lock');
  try {
    await writeFile(file, JSON.stringify({ pid: -1, childPid: process.pid }));
    await utimes(file, new Date(0), new Date(0));
    await assert.rejects(acquireLock(file, { timeoutMs: 20, pollMs: 10 }), /another task/);
    await writeFile(file, JSON.stringify({ pid: -1, childPid: null }));
    await utimes(file, new Date(0), new Date(0));
    const lock = await acquireLock(file, { pollMs: 10 });
    await lock.release();
    await assert.rejects(readFile(file), { code: 'ENOENT' });
    await writeFile(file, JSON.stringify({ pid: -1 }));
    await writeFile(`${file}.reclaim`, JSON.stringify({ pid: -1 }));
    await utimes(file, new Date(0), new Date(0));
    await utimes(`${file}.reclaim`, new Date(0), new Date(0));
    let active = 0, maxActive = 0;
    const contenders = await Promise.allSettled(Array.from({ length: 5 }, async () => {
      const contender = await acquireLock(file, { pollMs: 10 });
      active++; maxActive = Math.max(maxActive, active);
      await delay(5);
      active--;
      await contender.release();
    }));
    assert.deepEqual(contenders.filter(result => result.status === 'rejected').map(result => result.reason.message), []);
    assert.equal(maxActive, 1);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('existing PDFs and previews are never overwritten, before loading the engine', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'omaax-document-preserve-'));
  const input = path.join(dir, 'source.docx');
  const output = path.join(dir, 'report.pdf');
  try {
    await writeFile(input, 'original document');
    await writeFile(output, 'original PDF');
    await assert.rejects(convert({ command: 'pdf', input, output }), /already exists/);
    await assert.rejects(convert({ command: 'preview', input, output: dir }), /already exists/);
    assert.equal(await readFile(input, 'utf8'), 'original document');
    assert.equal(await readFile(output, 'utf8'), 'original PDF');
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('script failures preserve the exit code instead of reporting success', async () => {
  await assert.rejects(runProcess(process.execPath, ['-e', 'process.exit(7)'], { capture: true }), error => error.exitCode === 7);
  assert.equal(await runProcess(process.execPath, ['-e', 'process.stdout.write(process.argv[1])', 'spaces and 中文'], { capture: true }), 'spaces and 中文');
});

test('only Windows native missing-DLL failures suggest repairing the bundled component', () => {
  for (const status of ['0xc0000135', '3221225781', '-1073741515']) {
    const error = new Error(`LibreOffice helper returned an invalid response (exit ${status}).`);
    const mapped = documentConversionError(error, 'win32');
    assert.equal(mapped.code, 'DOCUMENT_NATIVE_RUNTIME_MISSING');
    assert.match(mapped.message, /Microsoft Visual C\+\+ v14/);
    assert.match(mapped.message, /Repair or reinstall OMaaX/);
    assert.equal(mapped.cause, error);
    assert.equal(documentConversionError(error, 'linux'), error);
  }
  for (const [code, message] of [['invalid-document', 'Invalid Office document 0xc0000135.docx'], ['timeout', 'LibreOffice helper timed out'], ['EPERM', 'spawn EPERM'], ['failed', 'LibreOffice native conversion failed: unsupported shape']]) {
    const error = Object.assign(new Error(message), { code });
    assert.equal(documentConversionError(error, 'win32'), error);
  }
});

test('Windows cancellation stops only the owned three-level process tree', { skip: process.platform !== 'win32' }, async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'omaax-document-cancel-'));
  const fixture = path.join(dir, 'child.cjs');
  const pids = path.join(dir, 'pids.jsonl');
  await writeFile(fixture, `const fs=require('node:fs');const {spawn}=require('node:child_process');const depth=Number(process.argv[2]);fs.appendFileSync(process.argv[3],JSON.stringify({pid:process.pid,depth})+'\\n');if(depth<2)spawn(process.execPath,[__filename,String(depth+1),process.argv[3]],{windowsHide:true,stdio:'ignore'});setInterval(()=>{},1000);`);
  const unrelated = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { windowsHide: true, stdio: 'ignore' });
  const controller = new AbortController();
  let ownedPid;
  const cancelled = runProcess(process.execPath, [fixture, '0', pids], { capture: true, signal: controller.signal, onSpawn: pid => { ownedPid = pid; } });
  const checkCancelled = assert.rejects(cancelled, /stop the fixture/);
  function isAlive(pid) { try { process.kill(pid, 0); return true; } catch { return false; } }
  try {
    let records = [];
    for (let attempt = 0; attempt < 250 && records.length < 3; attempt++) {
      await delay(20);
      try { records = (await readFile(pids, 'utf8')).trim().split('\n').map(line => JSON.parse(line)); } catch { /* A fixture may still be starting. */ }
    }
    assert.equal(records.length, 3, 'The child and grandchild must both exist before cancellation.');
    controller.abort(new Error('stop the fixture'));
    await checkCancelled;
    for (const record of records) assert.equal(isAlive(record.pid), false, `Owned depth ${record.depth} survived cancellation.`);
    assert.equal(isAlive(unrelated.pid), true, 'Unrelated processes must remain alive.');
  } finally {
    controller.abort(new Error('stop the fixture'));
    await checkCancelled.catch(() => {});
    if (ownedPid && isAlive(ownedPid)) spawnSync(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'), ['/PID', String(ownedPid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    unrelated.kill();
    await rm(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 30 });
  }
});
