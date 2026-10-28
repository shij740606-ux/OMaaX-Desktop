import { test } from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { runtimeEnvironment, prepare, fonts, worker } from '../resources/scripts/figure-pdf-runtime.mjs';
import { runProcess } from '../resources/documents/cli.mjs';

test('PDF environment excludes developer Python, providers, mirrors and proxies', () => {
  const env = runtimeEnvironment(path.resolve('isolated'), { PATH: 'original', PYTHONPATH: 'unsafe', PYTHONHOME: 'unsafe', VIRTUAL_ENV: 'unsafe', UV_PYTHON_INSTALL_MIRROR: 'unsafe', PIP_INDEX_URL: 'unsafe', HTTPS_PROXY: 'unsafe', http_proxy: 'unsafe', ALL_PROXY: 'unsafe', NODE_OPTIONS: 'unsafe', REVIEW_LLM_API_KEY: 'unsafe' });
  assert.equal(env.PATH, 'original');
  for (const key of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'UV_PYTHON_INSTALL_MIRROR', 'PIP_INDEX_URL', 'HTTPS_PROXY', 'http_proxy', 'ALL_PROXY', 'NODE_OPTIONS', 'REVIEW_LLM_API_KEY']) assert.equal(env[key], undefined, key);
  assert.equal(env.PYTHONDONTWRITEBYTECODE, '1');
});

test('concurrent first preparation installs once and publishes a verified marker', async () => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'omaax-pdf-runtime-'));
  const calls = [];
  const execute = async (executable, args, options) => {
    calls.push({ executable, args, env: options.env });
    if (args.includes('venv')) {
      const python = path.join(state, 'runtime', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
      await mkdir(path.dirname(python), { recursive: true }); await writeFile(python, 'fixture');
    }
    if (args.includes('sync')) await delay(80);
    return JSON.stringify({ ready: true, runtimeVersion: '1.25.5' });
  };
  try {
    const results = await Promise.all([prepare({ state, uv: 'fixture-uv' }, { execute }), prepare({ state, uv: 'fixture-uv' }, { execute })]);
    assert.ok(results.every(v => v.ready));
    const installs = calls.filter(v => v.args.includes('sync'));
    assert.equal(installs.length, 1);
    assert.ok(installs[0].args.includes('--require-hashes'));
    assert.ok(installs[0].args.includes('https://pypi.org/simple'));
    assert.equal((await readFile(path.join(state, 'runtime.sha256'), 'utf8')).length, 64);
  } finally { await rm(state, { recursive: true, force: true }); }
});

test('a failed install leaves no success marker and the next request retries', async () => {
  const state = await mkdtemp(path.join(os.tmpdir(), 'omaax-pdf-retry-'));
  let fail = true, installs = 0;
  const execute = async (_, args) => {
    if (args.includes('venv')) {
      const python = path.join(state, 'runtime', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
      await mkdir(path.dirname(python), { recursive: true }); await writeFile(python, 'fixture');
    }
    if (args.includes('sync')) { installs++; if (fail) throw Error('synthetic interrupted download'); }
    return JSON.stringify({ ready: true, runtimeVersion: '1.25.5' });
  };
  try {
    await assert.rejects(prepare({ state, uv: 'fixture-uv' }, { execute }), /interrupted/);
    await assert.rejects(readFile(path.join(state, 'runtime.sha256')));
    fail = false;
    assert.equal((await prepare({ state, uv: 'fixture-uv' }, { execute })).ready, true);
    assert.equal(installs, 2);
  } finally { await rm(state, { recursive: true, force: true }); }
});

test('font discovery returns real discovered names and no invented fallback', async () => {
  assert.deepEqual((await fonts({ platform: 'linux', execute: async () => 'Actual Font\nActual Font,Other\n' })).families, ['Actual Font', 'Other']);
  assert.deepEqual((await fonts({ platform: 'linux', execute: async () => { throw Error('unavailable'); } })).families, []);
});

test('macOS discovers installed AppKit families with Unicode names and caller cancellation', async () => {
  const signal = AbortSignal.timeout(20_000);
  const result = await fonts({ platform: 'darwin', signal, execute: async (executable, args, options) => {
    assert.equal(executable, '/usr/bin/osascript');
    assert.deepEqual(args.slice(0, 3), ['-l', 'JavaScript', '-e']);
    assert.match(args[3], /ObjC\.import\('AppKit'\)/);
    assert.match(args[3], /NSFontManager\.sharedFontManager\.availableFontFamilies/);
    assert.equal(options.signal, signal);
    assert.equal(options.capture, true);
    return JSON.stringify(['研究字体', 'Actual Font', 'Actual Font', '', null, 12]);
  } });
  assert.deepEqual(result.families, ['Actual Font', '研究字体']);
  assert.equal(result.warnings, undefined);
});

test('macOS font failures return no invented families and cancellation stays observable', async () => {
  for (const execute of [async () => { throw Error('unavailable'); }, async () => 'not JSON', async () => '{}']) {
    const result = await fonts({ platform: 'darwin', execute });
    assert.deepEqual(result.families, []);
    assert.ok(result.warnings.length > 0);
  }
  const controller = new AbortController();
  const reason = Error('font discovery cancelled');
  await assert.rejects(fonts({ platform: 'darwin', signal: controller.signal, execute: async (_, __, options) => {
    controller.abort(reason);
    throw options.signal.reason;
  } }), error => error === reason);
});

test('isolated Python worker returns Unicode names and warnings as UTF-8', async t => {
  const python = process.env.OMAAX_FIGURE_TEST_PYTHON || fileURLToPath(new URL('../../validation-local/edu-test-env/' + (process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'), import.meta.url));
  try { await access(python); } catch { return t.skip('Prepared local PyMuPDF test environment is unavailable'); }
  const state = await mkdtemp(path.join(os.tmpdir(), 'omaax-pdf-unicode-'));
  const env = runtimeEnvironment(state, { ...process.env, PYTHONHOME: 'invalid', PYTHONPATH: 'invalid' });
  try {
    const raw = await runProcess(python, ['-I', '-B', '-X', 'utf8', '-c', 'import pymupdf,base64; d=pymupdf.open(); p=d.new_page(); p.insert_text((30,30),"Measured 1.23"); print(base64.b64encode(d.tobytes()).decode())'], { env, capture: true });
    const inspected = await worker(python, { action: 'inspect', state, name: '科研结果.pdf', dataUrl: 'data:application/pdf;base64,' + raw.trim() }, { env, signal: AbortSignal.timeout(20_000) });
    assert.equal(inspected.name, '科研结果.pdf');
    const imported = await worker(python, { action: 'import', state, token: inspected.token, pages: [1], mode: 'editable', lang: 'zh', availableFonts: ['Different Font'] }, { env, signal: AbortSignal.timeout(20_000) });
    assert.match(imported.items[0].warnings.join('\n'), /本机未找到 PDF 字体/);
    assert.doesNotMatch(imported.items[0].warnings.join('\n'), /\ufffd/);
  } finally { await rm(state, { recursive: true, force: true }); }
});
