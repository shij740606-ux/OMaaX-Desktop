import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const verifier = fileURLToPath(new URL('./verify-installed-payload.ps1', import.meta.url));
const windows = process.platform === 'win32';
const powershell = windows ? path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe') : 'powershell';
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'omaax-payload-check-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const install = path.join(root, '安装 软件');
  fs.mkdirSync(path.join(install, 'resources'), { recursive: true });
  const contents = { 'OMaaX.exe': 'new executable', 'resources/runtime.bin': 'current runtime' };
  for (const [name, text] of Object.entries(contents)) fs.writeFileSync(path.join(install, name), text);
  const data = { version: '0.4.50', files: Object.entries(contents).map(([name, text]) => ({ path: name, bytes: Buffer.byteLength(text), sha256: createHash('sha256').update(text).digest('hex') })) };
  const manifest = path.join(root, 'manifest.json');
  const receipt = path.join(install, 'install-verification.json');
  return { root, install, manifest, data, receipt, run(extra = []) {
    fs.writeFileSync(manifest, JSON.stringify(data));
    return spawnSync(powershell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', verifier, '-InstallDir', install, '-Manifest', manifest, '-ExpectedVersion', '0.4.50', ...extra], { encoding: 'utf8', timeout: 30000, windowsHide: true });
  } };
}
function fail(result, pattern) {
  assert.equal(result.status, 1, `${result.error || ''}\n${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, pattern);
}

test('installer verifies complete payload and writes a receipt only after success', { skip: !windows }, t => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr);
  const receipt = JSON.parse(fs.readFileSync(f.receipt, 'utf8'));
  assert.equal(receipt.version, '0.4.50');
  assert.equal(receipt.count, 2);
  assert.equal(receipt.actualInstallDir.toLowerCase(), f.install.toLowerCase());
  assert.equal(receipt.installDir, f.install);
  assert.ok(Number.isFinite(Date.parse(receipt.verifiedAt)));
});

test('same-sized old executable is rejected and a stale success receipt is removed', { skip: !windows }, t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.install, 'OMaaX.exe'), 'old executable');
  fs.writeFileSync(f.receipt, '{"version":"old"}');
  fail(f.run(), /SHA256 mismatch/);
  assert.equal(fs.existsSync(f.receipt), false);
});

test('missing runtime and truncated payload fail without issuing a receipt', { skip: !windows }, t => {
  const f = fixture(t);
  fs.unlinkSync(path.join(f.install, 'resources/runtime.bin'));
  fail(f.run(), /verification failed/i);
  assert.equal(fs.existsSync(f.receipt), false);
  fs.writeFileSync(path.join(f.install, 'resources/runtime.bin'), 'short');
  fail(f.run(), /Size mismatch/);
  assert.equal(fs.existsSync(f.receipt), false);
});

test('manifest traversal, absolute paths, alternate streams and ambiguous paths are rejected', { skip: !windows }, t => {
  const f = fixture(t);
  for (const bad of ['../outside', 'resources/../../outside', 'C:\\outside', '/outside', 'OMaaX.exe:stream', 'resources\\.\\runtime.bin', 'resources\\runtime.bin.', 'resources\\CON']) {
    f.data.files[0].path = bad;
    fail(f.run(), /path/i);
    assert.equal(fs.existsSync(f.receipt), false);
  }
});

test('case-insensitive duplicate files and wrong installer versions are rejected', { skip: !windows }, t => {
  const f = fixture(t);
  f.data.files.push({ ...f.data.files[0], path: 'omaax.EXE' });
  fail(f.run(), /Duplicate payload/);
  f.data.files.pop();
  f.data.version = '0.4.41';
  fail(f.run(), /does not match installer version/);
  assert.equal(fs.existsSync(f.receipt), false);
});

test('receipt destination cannot overwrite payload or a file outside the install directory', { skip: !windows }, t => {
  const f = fixture(t);
  const outside = path.join(f.root, 'outside.json');
  fs.writeFileSync(outside, 'keep');
  fail(f.run(['-Receipt', outside]), /Receipt must be inside/);
  assert.equal(fs.readFileSync(outside, 'utf8'), 'keep');
  fail(f.run(['-Receipt', 'OMaaX.exe']), /cannot replace a payload/);
  assert.equal(fs.readFileSync(path.join(f.install, 'OMaaX.exe'), 'utf8'), 'new executable');
  assert.equal(fs.existsSync(f.receipt), false);
});

test('directory junctions cannot redirect payload validation or receipt writes', { skip: !windows }, t => {
  const f = fixture(t);
  const outside = path.join(f.root, 'outside');
  fs.renameSync(path.join(f.install, 'resources'), outside);
  try { fs.symlinkSync(outside, path.join(f.install, 'resources'), 'junction'); }
  catch (error) { if (['EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) { t.skip(error.message); return; } throw error; }
  fail(f.run(), /Reparse point/);
  assert.equal(fs.existsSync(f.receipt), false);
  fail(f.run(['-Receipt', 'resources/receipt.json']), /Reparse point/);
  assert.equal(fs.existsSync(path.join(outside, 'receipt.json')), false);
});

test('an installation root that is itself a junction is rejected', { skip: !windows }, t => {
  const f = fixture(t);
  const original = path.join(f.root, 'real-install');
  fs.renameSync(f.install, original);
  try { fs.symlinkSync(original, f.install, 'junction'); }
  catch (error) { if (['EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) { t.skip(error.message); return; } throw error; }
  fail(f.run(), /Reparse point/);
  assert.equal(fs.existsSync(f.receipt), false);
});

test('file symbolic links are rejected even when the target content matches', { skip: !windows }, t => {
  const f = fixture(t);
  const outside = path.join(f.root, 'outside-runtime.bin');
  fs.copyFileSync(path.join(f.install, 'resources/runtime.bin'), outside);
  fs.unlinkSync(path.join(f.install, 'resources/runtime.bin'));
  try { fs.symlinkSync(outside, path.join(f.install, 'resources/runtime.bin'), 'file'); }
  catch (error) { if (['EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) { t.skip(error.message); return; } throw error; }
  fail(f.run(), /Reparse point/);
  assert.equal(fs.existsSync(f.receipt), false);
  assert.equal(fs.readFileSync(outside, 'utf8'), 'current runtime');
});
