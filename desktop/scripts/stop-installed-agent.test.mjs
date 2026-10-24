import test from 'node:test';
import assert from 'node:assert/strict';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';

const script = fileURLToPath(new URL('./stop-installed-agent.ps1', import.meta.url));
const powershell = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
const run = (dir, manifest) => spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script, '-InstallDir', dir, ...(manifest ? ['-Manifest', manifest] : [])], { encoding: 'utf8', timeout: 30000, windowsHide: true });
const quote = text => `'${text.replaceAll("'", "''")}'`;
const fixture = () => mkdtempSync(path.join(tmpdir(), 'omaax-preinstall-'));
const removeFixture = dir => {
  assert.equal(path.dirname(dir), tmpdir());
  assert.ok(path.basename(dir).startsWith('omaax-preinstall-'));
  rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
};

test('preinstall accepts new or inactive installations without modifying their files', { skip: process.platform !== 'win32' }, () => {
  const dir = fixture();
  try {
    assert.equal(run(path.join(dir, 'new-install')).status, 0);
    writeFileSync(path.join(dir, 'OMaaX.exe'), 'old executable bytes');
    writeFileSync(path.join(dir, 'user-notes.txt'), 'keep this file');
    const result = run(dir);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readFileSync(path.join(dir, 'OMaaX.exe'), 'utf8'), 'old executable bytes');
    assert.equal(readFileSync(path.join(dir, 'user-notes.txt'), 'utf8'), 'keep this file');
  } finally { removeFixture(dir); }
});

test('preinstall refuses root, linked directories and locked core files', { skip: process.platform !== 'win32' }, () => {
  const dir = fixture();
  try {
    assert.notEqual(run(path.parse(dir).root).status, 0);
    mkdirSync(path.join(dir, 'actual'));
    symlinkSync(path.join(dir, 'actual'), path.join(dir, 'linked'), 'junction');
    assert.notEqual(run(path.join(dir, 'linked')).status, 0);
    const file = path.join(dir, 'OMaaX.exe');
    writeFileSync(file, 'locked bytes must survive');
    const command = `$h=[IO.File]::Open(${quote(file)},[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);try { & ${quote(script)} -InstallDir ${quote(dir)}; exit 0 } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 9 } finally { $h.Dispose() }`;
    const result = spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command], { encoding: 'utf8', timeout: 30000, windowsHide: true });
    assert.equal(result.status, 9, result.stderr);
    assert.match(result.stderr, /Cannot replace installation file/);
    assert.equal(readFileSync(file, 'utf8'), 'locked bytes must survive');
  } finally { removeFixture(dir); }
});

test('preinstall rejects linked receipt and payload destinations before changing external files', { skip: process.platform !== 'win32' }, () => {
  const dir = fixture();
  try {
    const target = path.join(dir, 'target'), external = path.join(dir, 'external');
    mkdirSync(target); mkdirSync(external);
    const protectedFile = path.join(external, 'verified.json');
    writeFileSync(protectedFile, 'outside installation');
    symlinkSync(external, path.join(target, 'installation'), 'junction');
    assert.notEqual(run(target).status, 0, 'receipt parent junction must fail even without a manifest');
    assert.equal(readFileSync(protectedFile, 'utf8'), 'outside installation');
    rmSync(path.join(target, 'installation'));
    symlinkSync(external, path.join(target, 'scripts'), 'junction');
    const manifest = path.join(dir, 'manifest.json');
    writeFileSync(manifest, JSON.stringify({ files: [{ path: 'scripts/new-file.js' }] }));
    assert.notEqual(run(target, manifest).status, 0, 'payload parent junction must fail');
    assert.equal(existsSync(path.join(external, 'new-file.js')), false);
    rmSync(path.join(target, 'scripts'));
    mkdirSync(path.join(target, 'script.js'));
    writeFileSync(manifest, JSON.stringify({ files: [{ path: 'script.js' }] }));
    assert.notEqual(run(target, manifest).status, 0, 'a directory cannot occupy a file destination');
    for (const bad of ['../external/verified.json', '/external/verified.json', 'scripts/../x', 'x:stream', 'CON.txt']) {
      writeFileSync(manifest, JSON.stringify({ files: [{ path: bad }] }));
      assert.notEqual(run(target, manifest).status, 0, bad);
    }
    writeFileSync(manifest, JSON.stringify({ files: [{ path: 'new/scripts/file.js' }] }));
    const result = run(target, manifest);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(readFileSync(protectedFile, 'utf8'), 'outside installation');
    assert.equal(existsSync(path.join(target, 'new')), false);
  } finally { removeFixture(dir); }
});

test('preinstall stops this installation only and waits for its executable locks to release', { skip: process.platform !== 'win32' }, async () => {
  const dir = fixture(), processes = [];
  try {
    const target = path.join(dir, 'target'), other = path.join(dir, 'other');
    for (const root of [target, other]) mkdirSync(path.join(root, 'node'), { recursive: true });
    const programs = [path.join(target, 'OMaaX.exe'), path.join(target, 'node', 'node.exe'), path.join(other, 'node', 'node.exe')];
    for (const exe of programs) {
      copyFileSync(process.execPath, exe);
      const child = spawn(exe, ['-e', 'console.log("ready");setInterval(()=>{},1000)'], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      processes.push(child);
      await once(child.stdout, 'data');
    }
    const result = run(target);
    assert.equal(result.status, 0, result.stderr);
    for (const child of processes.slice(0, 2)) {
      if (child.exitCode === null) await once(child, 'exit');
    }
    assert.equal(processes[2].exitCode, null, 'another installation must remain running');
    process.kill(processes[2].pid, 0);
    assert.ok(existsSync(programs[0]));
  } finally {
    for (const child of processes) if (child.exitCode === null) { child.kill(); await once(child, 'exit'); }
    removeFixture(dir);
  }
});
