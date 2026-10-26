#!/usr/bin/env node
// Local document tools, isolated from the analysis and education environments.
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { access, appendFile, copyFile, lstat, mkdir, mkdtemp, open, readFile, rename, rm, rmdir, stat, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const here = path.dirname(fileURLToPath(import.meta.url));
const pythonRelease = '3.12.12';
const kitVersion = '0.1.5';
const requirementsPath = path.join(here, 'requirements.txt');

export async function configureNativeRuntime(resources = here, env = process.env, platform = process.platform) {
  if (platform !== 'win32') return;
  const lock = JSON.parse(await readFile(path.join(resources, 'windows-crt.lock.json'), 'utf8'));
  const directory = path.join(resources, lock.helperDirectory);
  for (const [name, expected] of Object.entries(lock.files)) {
    const bytes = await readFile(path.join(directory, name)).catch(() => null);
    if (!bytes || bytes.length !== expected.bytes || createHash('sha256').update(bytes).digest('hex') !== expected.sha256) {
      throw Object.assign(new Error('The bundled document runtime is incomplete. Repair or reinstall OMaaX to restore its Windows runtime files.'), { code: 'DOCUMENT_NATIVE_RUNTIME_MISSING' });
    }
  }
  // LibreOffice Kit copies a deeply installed helper to a short temporary path.
  // Its child inherits PATH, so its verified CRT remains reachable in that case.
  const key = Object.keys(env).find(name => name.toLowerCase() === 'path') ?? 'PATH';
  env[key] = [directory, ...(env[key] ?? '').split(';').filter(value => value && value.toLowerCase() !== directory.toLowerCase())].join(';');
}

export function parseArguments(args) {
  const [command, ...rest] = args;
  if (command === 'prepare' && !rest.length) return { command };
  if (command === 'run' && rest[0] && !rest[0].startsWith('--')) {
    if (path.extname(rest[0]).toLowerCase() !== '.py') throw new Error('run expects a Python .py script.');
    return { command, script: path.resolve(rest[0]), args: rest.slice(1) };
  }
  if (command !== 'pdf' && command !== 'preview') {
    throw new Error('Usage: prepare | run <script.py> [args...] | pdf --input <file.docx|file.pptx> --output <new.pdf> | preview --input <file.docx|file.pptx|file.pdf> --output-dir <new-directory> [--pages 1,2] [--dpi 144]');
  }
  const allowed = command === 'pdf' ? ['input', 'output'] : ['input', 'output-dir', 'pages', 'dpi'];
  const options = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i].slice(2);
    if (!rest[i].startsWith('--') || !allowed.includes(key) || options[key] !== undefined || !rest[i + 1] || rest[i + 1].startsWith('--')) {
      throw new Error(`Invalid or repeated option: ${rest[i]}`);
    }
    options[key] = rest[i + 1];
  }
  const destination = command === 'pdf' ? 'output' : 'output-dir';
  if (!options.input || !options[destination]) throw new Error(`--input and --${destination} are required.`);
  const extensions = command === 'pdf' ? ['.docx', '.pptx'] : ['.docx', '.pptx', '.pdf'];
  if (!extensions.includes(path.extname(options.input).toLowerCase())) throw new Error(`Unsupported input. Expected ${extensions.join(', ')}.`);
  if (command === 'pdf' && path.extname(options.output).toLowerCase() !== '.pdf') throw new Error('PDF output must have a .pdf extension.');
  let pages;
  if (options.pages && options.pages !== 'all') {
    if (!/^[1-9]\d*(,[1-9]\d*)*$/.test(options.pages)) throw new Error('--pages must be all or one-based page numbers separated by commas.');
    pages = [...new Set(options.pages.split(',').map(Number))];
    if (pages.some(n => !Number.isSafeInteger(n))) throw new Error('Invalid page number.');
  }
  const dpi = options.dpi === undefined ? 144 : Number(options.dpi);
  if (!Number.isInteger(dpi) || dpi < 24 || dpi > 600) throw new Error('--dpi must be an integer from 24 to 600.');
  return { command, input: path.resolve(options.input), output: path.resolve(options[destination]), pages, dpi };
}

export function runtimeHome(env = process.env) {
  const value = env.OMAAX_DOCUMENT_HOME;
  if (!value || !path.isAbsolute(value) || path.resolve(value) === path.parse(value).root) {
    throw new Error('OMaaX document runtime location is missing. Restart the agent from OMaaX and retry.');
  }
  return path.resolve(value);
}

export function isolatedPythonEnv(home, env = process.env) {
  const clean = { ...env };
  for (const name of Object.keys(clean)) {
    if (/^(VIRTUAL_ENV|PYTHONHOME|PYTHONPATH|UV_PROJECT_ENVIRONMENT|UV_INDEX|UV_DEFAULT_INDEX|UV_INDEX_URL|UV_EXTRA_INDEX_URL|PIP_INDEX_URL|PIP_EXTRA_INDEX_URL|UV_PYTHON_INSTALL_MIRROR|HTTPS?_PROXY|ALL_PROXY)$/i.test(name)) delete clean[name];
  }
  return {
    ...clean,
    UV_CACHE_DIR: path.join(home, 'cache'),
    UV_PYTHON_INSTALL_DIR: path.join(home, 'python'),
    UV_NO_PROGRESS: '1',
    UV_HTTP_TIMEOUT: '120',
    UV_LINK_MODE: 'copy',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONNOUSERSITE: '1',
  };
}

function alive(pid) {
  if (!Number.isInteger(pid) || pid < 1) return false;
  try { process.kill(pid, 0); return true; } catch (error) { return error.code === 'EPERM'; }
}

async function staleLock(file) {
  const age = Date.now() - (await stat(file)).mtimeMs;
  let owner;
  try { owner = JSON.parse(await readFile(file, 'utf8')); } catch { /* A process may still be writing its lock. */ }
  return (owner && !alive(owner.pid) && !alive(owner.childPid) && age > 1000) || (!owner && age > 5 * 60_000);
}

async function releaseOwnedLock(file, token) {
  for (let attempt = 0; ; attempt++) {
    try {
      const owner = JSON.parse(await readFile(file, 'utf8'));
      if (owner.token === token) await unlink(file);
      return;
    } catch (error) {
      if (error.code === 'ENOENT') return;
      // Windows readers can briefly deny unlink while checking this same lock.
      if (!['EPERM', 'EBUSY'].includes(error.code) || attempt >= 40) throw error;
      await delay(25);
    }
  }
}

export async function acquireLock(file, { signal, timeoutMs = 30 * 60_000, pollMs = 300 } = {}) {
  const token = randomUUID();
  const started = Date.now();
  const owner = { pid: process.pid, token, createdAt: new Date().toISOString() };
  while (true) {
    signal?.throwIfAborted();
    try {
      const handle = await open(file, 'wx', 0o600);
      try { await handle.writeFile(JSON.stringify(owner)); } finally { await handle.close(); }
      return {
        async child(pid) { await writeFile(file, JSON.stringify({ ...owner, childPid: pid }), { mode: 0o600 }); },
        async release() { await releaseOwnedLock(file, token); },
      };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      try {
        if (await staleLock(file)) {
          // Serialize stale cleanup so a second waiter cannot delete the new owner's lock.
          let cleanup;
          try {
            cleanup = await open(`${file}.reclaim`, 'wx', 0o600);
            await cleanup.writeFile(JSON.stringify(owner));
            if (await staleLock(file)) await unlink(file);
          } catch (cleanupError) {
            if (!['EEXIST', 'ENOENT', 'EPERM', 'EBUSY'].includes(cleanupError.code)) throw cleanupError;
            if (cleanupError.code === 'EEXIST' && await staleLock(`${file}.reclaim`)) await unlink(`${file}.reclaim`);
          } finally {
            if (cleanup) { await cleanup.close(); await releaseOwnedLock(`${file}.reclaim`, token); }
          }
        }
      } catch (inspectError) {
        if (inspectError.code === 'ENOENT') continue;
        if (!['EPERM', 'EBUSY'].includes(inspectError.code)) throw inspectError;
      }
      if (Date.now() - started >= timeoutMs) throw new Error('Document environment is being prepared by another task. Retry after it completes.');
      await delay(pollMs, undefined, { signal });
    }
  }
}

export async function runProcess(executable, args, { env, signal, capture = false, stdio = 'inherit', onSpawn, cwd = process.cwd() } = {}) {
  signal?.throwIfAborted();
  return await new Promise((resolve, reject) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true, shell: false, signal: process.platform === 'win32' ? undefined : signal, stdio: capture ? ['ignore', 'pipe', 'pipe'] : stdio });
    let stdout = '', stderr = '', failure;
    let spawned = Promise.resolve(), stopping = Promise.resolve(), stopRequested = false;
    const stop = reason => {
      failure ??= reason instanceof Error ? reason : new Error('Document operation cancelled.');
      if (stopRequested || !child.pid || child.exitCode !== null || child.signalCode !== null) return;
      stopRequested = true;
      if (process.platform !== 'win32') { child.kill(); return; }
      // Terminating just the CLI would orphan uv/native helpers on Windows. Target only this owned PID tree.
      stopping = new Promise((done, failed) => {
        const killer = spawn(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'), ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, shell: false, stdio: 'ignore' });
        killer.once('error', failed);
        killer.once('close', code => code === 0 ? done() : failed(new Error(`Owned document process tree could not be fully stopped (taskkill ${code}).`)));
      }).catch(error => { failure = error; child.kill(); });
    };
    const abort = () => stop(signal.reason);
    if (process.platform === 'win32' && signal) {
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) abort();
    }
    if (capture) {
      child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
      child.stdout.on('data', data => { stdout = (stdout + data).slice(-256_000); });
      child.stderr.on('data', data => { stderr = (stderr + data).slice(-16_000); });
    }
    child.once('spawn', () => { spawned = Promise.resolve(onSpawn?.(child.pid)).catch(stop); });
    child.once('error', error => { failure = error; });
    child.once('close', async (code, exitSignal) => {
      signal?.removeEventListener('abort', abort);
      await spawned;
      await stopping;
      if (failure) reject(failure);
      else if (code === 0) resolve(stdout);
      else {
        const error = new Error(`${path.basename(executable)} exited with ${code ?? exitSignal}.${stderr ? `\n${stderr.trim()}` : ''}`);
        error.exitCode = typeof code === 'number' && code > 0 ? code : 1;
        reject(error);
      }
    });
  });
}

async function loadKit() {
  // Resolve only the staged, pinned package; never fall through to a newer DSH tree.
  const root = path.join(here, 'node_modules', '@deepseek-ai', 'libreoffice-kit');
  let manifest;
  try { manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8')); }
  catch { throw new Error('The document conversion component is missing. Prepare the OMaaX document resources or reinstall the current OMaaX package.'); }
  if (manifest.version !== kitVersion) throw new Error(`Document converter version mismatch: expected ${kitVersion}.`);
  return await import(pathToFileURL(path.join(root, 'lib', 'index.js')).href);
}

async function pythonInfo(python, env, expected, signal) {
  const script = 'import json,sys,importlib.metadata as m; import docx,pptx,PIL,pypdf; print(json.dumps({"pythonVersion":sys.version.split()[0],"packages":{n:m.version(n) for n in json.loads(sys.argv[1])}}))';
  try {
    const info = JSON.parse(await runProcess(python, ['-I', '-c', script, JSON.stringify(Object.keys(expected))], { env, signal, capture: true }));
    if (info.pythonVersion !== pythonRelease || Object.entries(expected).some(([name, version]) => info.packages[name] !== version)) return null;
    return info;
  } catch (error) {
    signal?.throwIfAborted();
    // Permission/pipe failures are not evidence of damaged packages: do not replace the environment.
    if (error.code && error.code !== 'ENOENT') throw error;
    return null;
  }
}

async function ensureHome(home) {
  await mkdir(home, { recursive: true });
  if ((await lstat(home)).isSymbolicLink()) throw new Error('Document runtime directory must not be a symbolic link.');
}

export async function prepare({ signal } = {}) {
  const home = runtimeHome();
  await configureNativeRuntime();
  const kit = await loadKit();
  const converter = await kit.discoverRuntime();
  const requirements = await readFile(requirementsPath, 'utf8');
  const expected = Object.fromEntries(requirements.split(/\r?\n/).filter(line => line && !line.startsWith('#')).map(line => line.split('==')));
  const id = createHash('sha256').update(`isolated-copy-v1\n${pythonRelease}\n${requirements}`).digest('hex').slice(0, 16);
  await ensureHome(home);
  const directory = path.join(home, `venv-${id}`);
  const python = path.join(directory, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const env = isolatedPythonEnv(home);
  let info = await pythonInfo(python, env, expected, signal);
  if (!info) {
    const uv = process.env.OMAAX_DOCUMENT_UV;
    if (!uv || !path.isAbsolute(uv)) throw new Error('The bundled Python preparation tool is unavailable. Restart OMaaX and retry.');
    await access(uv);
    const lock = await acquireLock(path.join(home, '.prepare.lock'), { signal });
    try {
      info = await pythonInfo(python, env, expected, signal);
      if (!info) {
        let repairing = false;
        try {
          const existing = await lstat(directory);
          if (existing.isSymbolicLink()) throw new Error('Refusing to replace a linked document environment.');
          // Preserve an incomplete environment for diagnosis; never remove user project files.
          await rename(directory, `${directory}.incomplete-${Date.now()}-${randomUUID().slice(0, 8)}`);
          repairing = true;
        } catch (error) { if (error.code !== 'ENOENT') throw error; }
        process.stderr.write('Preparing the dedicated OMaaX document environment. First use may require a download.\n');
        const runUv = async args => {
          try { await runProcess(uv, ['--no-config', ...args], { cwd: home, env, signal, stdio: ['ignore', 2, 2], onSpawn: pid => lock.child(pid) }); }
          finally { await lock.child(null); }
        };
        if (repairing) await runUv(['cache', 'clean', ...Object.keys(expected)]);
        await runUv(['venv', '--python', pythonRelease, '--python-preference', 'only-managed', directory]);
        await runUv(['pip', 'sync', '--python', python, '--index-url', 'https://pypi.org/simple', requirementsPath]);
        info = await pythonInfo(python, env, expected, signal);
        if (!info) throw new Error('Document packages did not pass verification. Retry prepare to repair the dedicated environment.');
      }
    } finally { await lock.release(); }
  }
  return {
    ready: true, python, ...info, runtimeHome: home,
    converter: {
      version: converter.version, backend: converter.backend,
      engineChecked: false, validation: 'on-export',
      requirements: [],
      windowsRuntime: process.platform === 'win32' ? 'bundled-app-local' : undefined,
    },
  };
}

async function freshDestination(input, output) {
  const source = await stat(input);
  if (!source.isFile()) throw new Error('Input must be a local document file.');
  if (input === output) throw new Error('Output must be different from the original file.');
  try { await lstat(output); } catch (error) { if (error.code === 'ENOENT') return; throw error; }
  throw new Error('Output already exists. Use a new revision filename or preview directory; original files are preserved.');
}

export function documentConversionError(error, platform = process.platform) {
  const detail = `${error.message ?? ''} ${error.cause?.message ?? ''}`;
  const engineFailure = ['failed', 'unavailable', undefined].includes(error.code) && /LibreOffice|native|helper|vcruntime|msvcp/i.test(detail);
  const missingDll = /0xc0000135|\b3221225781\b|-1073741515\b/i.test(detail)
    || /(?:vcruntime140(?:_1)?|msvcp140)\.dll.*(?:missing|not found|could not|无法|找不到)/i.test(detail);
  if (platform !== 'win32' || !engineFailure || !missingDll) return error;
  const mapped = new Error('The local document converter could not start because a bundled Windows runtime DLL is missing. Repair or reinstall OMaaX to restore its document component and Microsoft Visual C++ v14 runtime files, then retry. Microsoft Office, WPS and a separate runtime installation are not required.', { cause: error });
  mapped.code = 'DOCUMENT_NATIVE_RUNTIME_MISSING';
  return mapped;
}

export async function convert(options, { signal } = {}) {
  await freshDestination(options.input, options.output);
  await configureNativeRuntime();
  const kit = await loadKit();
  const home = runtimeHome();
  await ensureHome(home);
  let converter, temporary, previewReserved = false;
  try {
    if (options.command === 'preview') {
      await mkdir(options.output, { mode: 0o700 });
      previewReserved = true;
    }
    temporary = await mkdtemp(path.join(home, 'conversion-'));
    converter = await kit.createConverter({ timeoutMs: 120_000, maxImageResolution: 300, fontMetadataCacheDirectory: path.join(home, 'font-cache') });
    if (options.command === 'pdf') {
      const rendered = path.join(temporary, 'document.pdf');
      const result = await converter.render({ inputPath: options.input, outputPath: rendered }, signal);
      signal?.throwIfAborted();
      // Engine output stays private. The final publish itself must reject concurrent writers.
      await copyFile(rendered, options.output, constants.COPYFILE_EXCL);
      return { ...result, engineChecked: true, output: options.output, bytes: (await stat(rendered)).size };
    }
    const rendered = path.join(temporary, 'pages');
    const result = await converter.renderImages({ inputPath: options.input, outputDir: rendered, pages: options.pages ?? 'all', dpi: options.dpi }, signal);
    const images = [];
    for (const image of result.images) {
      signal?.throwIfAborted();
      if (path.dirname(path.resolve(image.path)) !== rendered) throw new Error('Unexpected preview file location.');
      const output = path.join(options.output, path.basename(image.path));
      await copyFile(image.path, output, constants.COPYFILE_EXCL);
      images.push({ ...image, path: output });
    }
    const published = { ...result, engineChecked: true, images };
    // A manifest is published last, so partial batches cannot appear complete.
    await writeFile(path.join(options.output, 'manifest.json'), JSON.stringify(published, null, 2), { flag: 'wx', mode: 0o600 });
    return published;
  } catch (error) {
    throw documentConversionError(error);
  } finally {
    try { await converter?.dispose(); }
    finally {
      if (temporary && path.dirname(temporary) === home && path.basename(temporary).startsWith('conversion-')) {
        await rm(temporary, { recursive: true, force: true }).catch(error => process.stderr.write(`Document scratch cleanup failed: ${error.message}\n`));
      }
      if (previewReserved) {
        // Only remove our reservation if still empty; never recursively remove a destination.
        await rmdir(options.output).catch(error => { if (!['ENOTEMPTY', 'EEXIST', 'ENOENT'].includes(error.code)) process.stderr.write(`Preview reservation cleanup failed: ${error.message}\n`); });
      }
    }
  }
}

async function audit(home, record) {
  try {
    await ensureHome(home);
    await appendFile(path.join(home, 'audit.jsonl'), `${JSON.stringify({ at: new Date().toISOString(), ...record })}\n`, { mode: 0o600 });
  } catch (error) { process.stderr.write(`Document audit could not be recorded: ${error.message}\n`); }
}

export async function main(args = process.argv.slice(2)) {
  const options = parseArguments(args);
  const home = runtimeHome();
  const controller = new AbortController();
  const stop = () => controller.abort(new Error('Document operation cancelled.'));
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
  const context = { command: options.command, script: options.script, input: options.input, output: options.output, cwd: process.cwd() };
  await audit(home, { ...context, status: 'started' });
  try {
    let result;
    if (options.command === 'prepare') result = await prepare({ signal: controller.signal });
    else if (options.command === 'run') {
      if (!(await stat(options.script)).isFile()) throw new Error('Python script must be a local file.');
      const runtime = await prepare({ signal: controller.signal });
      await runProcess(runtime.python, ['-E', '-s', '-B', '-X', 'utf8', '-u', options.script, ...options.args], { env: isolatedPythonEnv(home), signal: controller.signal });
      result = { completed: true, script: options.script, workingDirectory: process.cwd() };
    } else result = await convert(options, { signal: controller.signal });
    await audit(home, { ...context, status: 'completed' });
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return result;
  } catch (error) {
    await audit(home, { ...context, status: controller.signal.aborted ? 'cancelled' : 'failed', error: error.message });
    throw error;
  } finally {
    process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(`${JSON.stringify({ error: error.message, code: error.code ?? 'DOCUMENT_OPERATION_FAILED' })}\n`);
    process.exitCode = error.exitCode ?? 1;
  });
}
