// Minimal, cached PDF worker runtime. The application supplies every local path.
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { access, mkdir, readFile, writeFile, rename, unlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { acquireLock, runProcess, configureNativeRuntime } from '../documents/cli.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const requirements = path.join(here, 'figure-pdf-requirements.txt');
const release = '3.12.12';
const version = '1.25.5';

export function runtimeEnvironment(state, inherited = process.env) {
  const env = { ...inherited };
  for (const key of Object.keys(env)) {
    if (/^(PYTHON|UV_|PIP_|REVIEW_|DOCGEN_LLM_)|^(VIRTUAL_ENV|NODE_OPTIONS|HTTPS?_PROXY|ALL_PROXY)$/i.test(key)) delete env[key];
  }
  return { ...env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
    UV_CACHE_DIR: path.join(state, 'cache'), UV_PYTHON_INSTALL_DIR: path.join(state, 'python'),
    UV_NO_PROGRESS: '1', UV_HTTP_TIMEOUT: '120', UV_LINK_MODE: 'copy' };
}

function localState(value) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || path.resolve(value) === path.parse(path.resolve(value)).root) throw Error('Invalid PDF runtime location');
  return path.resolve(value);
}

export async function prepare(config, { signal, execute = runProcess } = {}) {
  const state = localState(config.state);
  await mkdir(state, { recursive: true, mode: 0o700 });
  const env = runtimeEnvironment(state);
  // The same verified app-local CRT used by the document and education runtimes.
  await configureNativeRuntime(config.documents ?? path.join(here, '..', 'documents'), env);
  const python = path.join(state, 'runtime', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const marker = path.join(state, 'runtime.sha256');
  const digest = createHash('sha256').update(await readFile(requirements)).update(release).digest('hex');
  const verify = async () => {
    const output = await execute(python, ['-I', '-B', '-c', 'import sys,pymupdf,json; assert sys.version_info[:2]==(3,12); assert pymupdf.VersionBind=="1.25.5"; d=pymupdf.open(); p=d.new_page(); p.get_pixmap(); print(json.dumps({"ready":True,"runtimeVersion":pymupdf.VersionBind}))'],
      { env, signal, capture: true, cwd: state });
    const result = JSON.parse(output.trim());
    if (result.ready !== true || result.runtimeVersion !== version) throw Error('PDF runtime verification failed');
    return { ...result, python, env };
  };
  const lock = await acquireLock(path.join(state, 'prepare.lock'), { signal, timeoutMs: 10 * 60_000 });
  try {
    if (await readFile(marker, 'utf8').catch(() => '') === digest) {
      try { return await verify(); } catch (error) { if (signal?.aborted) throw error; }
    }
    await unlink(marker).catch(error => { if (error.code !== 'ENOENT') throw error; });
    const run = args => execute(config.uv, ['--no-config', ...args], { env, signal, capture: true, cwd: state, onSpawn: pid => lock.child(pid) });
    try {
      await access(python);
      await execute(python, ['-I', '-B', '-c', 'import sys; assert sys.version_info[:2]==(3,12)'], { env, signal, capture: true, cwd: state });
    } catch (error) {
      if (signal?.aborted) throw error;
      await run(['venv', '--allow-existing', '--python', release, '--python-preference', 'only-managed', path.join(state, 'runtime')]);
    }
    await run(['pip', 'sync', '--python', python, '--index-url', 'https://pypi.org/simple', '--only-binary', ':all:', '--require-hashes', '--reinstall-package', 'PyMuPDF', requirements]);
    const result = await verify();
    await writeFile(marker + '.tmp', digest, { mode: 0o600 });
    await rename(marker + '.tmp', marker);
    return result;
  } finally { await lock.release(); }
}

export async function fonts({ signal, execute = runProcess, platform = process.platform } = {}) {
  try {
    let raw, families;
    if (platform === 'win32') {
      const command = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); Add-Type -AssemblyName System.Drawing; $fonts=New-Object System.Drawing.Text.InstalledFontCollection; ConvertTo-Json -Compress -InputObject @($fonts.Families | ForEach-Object {$_.Name} | Sort-Object -Unique)";
      raw = await execute(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe'),
        ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command], { signal, capture: true, env: runtimeEnvironment(path.resolve(here)) });
      families = JSON.parse(raw.trim());
    } else if (platform === 'darwin') {
      // AppKit lists installed families without requiring Xcode or third-party font tools.
      const command = "ObjC.import('AppKit'); JSON.stringify(ObjC.deepUnwrap($.NSFontManager.sharedFontManager.availableFontFamilies));";
      raw = await execute('/usr/bin/osascript', ['-l', 'JavaScript', '-e', command], { signal, capture: true });
      families = JSON.parse(raw.trim());
    } else {
      // Do not invent available families on systems without a font-discovery utility.
      raw = await execute('/usr/bin/fc-list', ['--format', '%{family}\n'], { signal, capture: true });
      families = raw.split(/[\r\n,]+/);
    }
    return { families: [...new Set(families.filter(v => typeof v === 'string' && v.trim().length > 0 && v.length < 200).map(v => v.trim()))].sort() };
  } catch (error) {
    if (signal?.aborted) throw error;
    return { families: [], warnings: ['System font discovery is unavailable; existing text remains unchanged.'] };
  }
}

export async function worker(python, request, { env, signal, onSpawn } = {}) {
  return await new Promise((resolve, reject) => {
    const child = spawn(python, ['-I', '-B', '-X', 'utf8', '-u', path.join(here, 'figure-pdf-worker.py')], { env, windowsHide: true, shell: false, stdio: ['pipe', 'pipe', 'pipe'] });
    const chunks = []; let size = 0, failure, stderr = '';
    const stop = () => { failure ??= signal?.reason ?? Error('PDF operation cancelled'); child.kill(); };
    signal?.addEventListener('abort', stop, { once: true });
    child.once('spawn', () => { Promise.resolve(onSpawn?.(child.pid)).catch(error => { failure = error; stop(); }); if (signal?.aborted) stop(); });
    child.stdout.on('data', data => { size += data.length; if (size > 65_000_000) { failure = Error('PDF result is too large'); child.kill(); } else chunks.push(data); });
    child.stderr.on('data', data => { stderr = (stderr + data).slice(-8000); });
    child.stdin.on('error', error => { if (error.code !== 'EPIPE') failure ??= error; });
    child.once('error', error => { failure = error; });
    child.once('close', code => {
      signal?.removeEventListener('abort', stop);
      if (failure) return reject(failure);
      try {
        const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
        if (value.error) throw Error(value.error);
        if (code !== 0) throw Error('PDF conversion failed');
        resolve(value);
      } catch (error) { reject(error instanceof SyntaxError ? Error('PDF conversion did not return a valid result') : error); }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

export async function main(input) {
  if (input.action === 'fonts') return fonts({ signal: AbortSignal.timeout(20_000) });
  const state = localState(input.state), en = input.lang === 'en';
  let runtime;
  try { runtime = await prepare(input, { signal: AbortSignal.timeout(10 * 60_000) }); }
  catch (error) {
    await writeFile(path.join(state, 'prepare-error.log'), String(error.message).slice(-8000), { mode: 0o600 }).catch(() => {});
    throw Error(en ? 'The PDF component could not be prepared. Check your connection and retry; downloaded files are cached.' : 'PDF 组件准备未完成，请检查网络后重试；已下载内容会保留供重试。');
  }
  if (input.action === 'prepare') return { ready: true, runtimeVersion: runtime.runtimeVersion };
  const cache = path.join(state, 'imports');
  await mkdir(cache, { recursive: true, mode: 0o700 });
  const lock = await acquireLock(path.join(state, 'worker.lock'), { signal: AbortSignal.timeout(4 * 60_000), timeoutMs: 4 * 60_000 });
  try {
    const availableFonts = input.action === 'import' && input.mode === 'editable' ? (await fonts({ signal: AbortSignal.timeout(20_000) })).families : [];
    return await worker(runtime.python, { ...input, state: cache, availableFonts }, { env: runtime.env, signal: AbortSignal.timeout(120_000), onSpawn: pid => lock.child(pid) });
  } finally { await lock.release(); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let input = '', bytes = 0;
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', data => { bytes += Buffer.byteLength(data); if (bytes > 68_000_000) { process.stderr.write('PDF request is too large'); process.exit(1); } input += data; });
  process.stdin.on('end', () => {
    Promise.resolve().then(() => main(JSON.parse(input))).then(result => process.stdout.write(JSON.stringify(result)), error => { process.stdout.write(JSON.stringify({ error: error.message })); process.exitCode = 1; });
  });
}
