import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const desktop = fileURLToPath(new URL('../', import.meta.url));
const makensis = path.join(process.env.LOCALAPPDATA || '', 'tauri/NSIS/makensis.exe');
const utils = path.join(desktop, 'src-tauri/target/release/nsis/x64/utils.nsh');
const enabled = process.platform === 'win32' && fs.existsSync(makensis) && fs.existsSync(utils);
const reg = path.join(process.env.SystemRoot || 'C:/Windows', 'System32/reg.exe');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
// NSIS treats dollars as variables even inside quoted paths.
const nsisPath = value => value.replaceAll('$', '$$').replaceAll('"', '$\\"');
function run(file, args, timeout = 90000) {
  const result = spawnSync(file, args, { encoding: 'utf8', timeout, windowsHide: true });
  assert.ifError(result.error);
  return result;
}

test('real NSIS hooks verify fresh installs and upgrades, reject tampering, and preserve user data', { skip: !enabled }, t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'omaax-nsis-hooks-'));
  const registryKey = `HKCU\\Software\\OMaaXInstallerHookTests\\${randomUUID()}`;
  const subKey = registryKey.slice('HKCU\\'.length);
  t.after(() => {
    run(reg, ['delete', registryKey, '/f', '/reg:32']);
    fs.rmSync(root, { recursive: true, force: true });
  });
  const fixtureDesktop = path.join(root, 'desktop');
  const hookDir = path.join(fixtureDesktop, 'src-tauri');
  const scripts = path.join(fixtureDesktop, 'scripts');
  const install = path.join(root, '安装 target');
  const userData = path.join(root, '用户 data');
  for (const dir of [hookDir, scripts, install, userData]) fs.mkdirSync(dir, { recursive: true });
  for (const script of ['stop-installed-agent.ps1', 'verify-installed-payload.ps1'])
    fs.copyFileSync(path.join(desktop, 'scripts', script), path.join(scripts, script));
  const sentinel = { 'settings.json': '{"server":"keep-existing","theme":"dark"}', 'conversations.jsonl': '{"message":"existing conversation"}\n' };
  for (const [name, text] of Object.entries(sentinel)) fs.writeFileSync(path.join(userData, name), text);
  // Also retain unknown files inside the installation directory: hooks may only
  // verify the package manifest, never clean arbitrary user-created contents.
  fs.writeFileSync(path.join(install, 'notes.txt'), 'keep local notes');

  const current = Buffer.from('current OMaaX fixture');
  const runtime = Buffer.from('current runtime fixture');
  const sourceExe = path.join(root, 'OMaaX.exe');
  const sourceRuntime = path.join(root, 'runtime.bin');
  fs.writeFileSync(sourceExe, current);
  fs.writeFileSync(sourceRuntime, runtime);
  const manifest = path.join(root, 'fixture-manifest.json');
  fs.writeFileSync(manifest, JSON.stringify({ version: '0.4.50', files: [
    { path: 'OMaaX.exe', bytes: current.length, sha256: sha(current) },
    { path: 'runtime.bin', bytes: runtime.length, sha256: sha(runtime) },
  ] }));
  const originalHook = fs.readFileSync(path.join(desktop, 'src-tauri/nsis-hooks.nsh'), 'utf8');
  const manifestSource = '"${OMAAX_HOOK_DIR}\\..\\installer-payload\\manifest.json"';
  assert.equal(originalHook.split(manifestSource).length, 2, 'fixture must replace exactly the manifest source');
  fs.writeFileSync(path.join(hookDir, 'nsis-hooks.nsh'), originalHook.replace(manifestSource, `"${nsisPath(manifest)}"`));

  const product = `OMaaX-Install-Test-${randomUUID()}`;
  const build = tamper => {
    const output = path.join(root, tamper ? 'tampered-test.exe' : 'valid-test.exe');
    const nsi = path.join(root, tamper ? 'tampered.nsi' : 'valid.nsi');
    fs.writeFileSync(nsi, `Unicode true
RequestExecutionLevel user
SilentInstall silent
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "Win\\COM.nsh"
!include "Win\\Propkey.nsh"
!include "${nsisPath(utils)}"
!define PRODUCTNAME "${product}"
!define MAINBINARYNAME "OMaaX"
!define VERSION "0.4.50"
!define STARTMENUFOLDER ""
!define UNINSTKEY "${subKey}"
!include "${nsisPath(path.join(hookDir, 'nsis-hooks.nsh'))}"
Name "${product}"
OutFile "${nsisPath(output)}"
InstallDir "${nsisPath(install)}"
Section
  SetShellVarContext current
  SetRegView 32
  SetOutPath "$INSTDIR"
  !insertmacro NSIS_HOOK_PREINSTALL
  File /oname=OMaaX.exe "${nsisPath(sourceExe)}"
  File /oname=runtime.bin "${nsisPath(sourceRuntime)}"
  CreateDirectory "$INSTDIR\\installation"
  WriteRegStr SHCTX "\${UNINSTKEY}" "DisplayName" "\${PRODUCTNAME}"
  WriteRegStr SHCTX "\${UNINSTKEY}" "DisplayVersion" "\${VERSION}"
  ${tamper ? 'FileOpen $2 "$INSTDIR\\OMaaX.exe" w\n  FileWrite $2 "tamper! OMaaX fixture"\n  FileClose $2' : ''}
  !insertmacro NSIS_HOOK_POSTINSTALL
SectionEnd
`, 'utf8');
    const compiled = run(makensis, ['/V2', '/INPUTCHARSET', 'UTF8', nsi]);
    assert.equal(compiled.status, 0, compiled.stdout + compiled.stderr);
    return output;
  };
  const query = name => run(reg, ['query', registryKey, '/v', name, '/reg:32']);
  const state = name => { const value = query(name); assert.equal(value.status, 0, value.stdout + value.stderr); return value.stdout; };
  const receipt = path.join(install, 'installation/verified.json');
  const assertPreserved = () => {
    for (const [name, text] of Object.entries(sentinel)) assert.equal(fs.readFileSync(path.join(userData, name), 'utf8'), text);
    assert.equal(fs.readFileSync(path.join(install, 'notes.txt'), 'utf8'), 'keep local notes');
  };
  const goodInstaller = build(false);
  let result = run(goodInstaller, ['/S']);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(state('InstallState'), /complete/);
  assert.match(state('DisplayVersion'), /0\.4\.50/);
  assert.equal(JSON.parse(fs.readFileSync(receipt, 'utf8')).count, 2);
  assert.equal(sha(fs.readFileSync(path.join(install, 'OMaaX.exe'))), sha(current));
  assertPreserved();

  fs.writeFileSync(path.join(install, 'OMaaX.exe'), Buffer.alloc(current.length, 120));
  fs.writeFileSync(path.join(install, 'runtime.bin'), Buffer.alloc(runtime.length, 121));
  run(reg, ['add', registryKey, '/v', 'DisplayVersion', '/t', 'REG_SZ', '/d', '0.4.41', '/f', '/reg:32']);
  result = run(goodInstaller, ['/S']);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(sha(fs.readFileSync(path.join(install, 'OMaaX.exe'))), sha(current));
  assert.equal(sha(fs.readFileSync(path.join(install, 'runtime.bin'))), sha(runtime));
  assert.match(state('InstallState'), /complete/);
  assert.match(state('DisplayVersion'), /0\.4\.50/);
  assertPreserved();

  result = run(build(true), ['/S']);
  assert.equal(result.status, 2, result.stdout + result.stderr);
  assert.match(state('InstallState'), /failed/);
  assert.notEqual(query('DisplayVersion').status, 0, 'failure must remove the claimed installed version');
  assert.equal(fs.existsSync(receipt), false, 'failure must remove a stale success receipt');
  assertPreserved();
});
