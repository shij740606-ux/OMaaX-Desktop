import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { stageDocumentCrt } from './stage-document-crt.mjs';
import { configureNativeRuntime } from '../resources/documents/cli.mjs';

function fixture() {
  const root = mkdtempSync(path.join(os.tmpdir(), 'omaax-crt-stage-'));
  const runtime = path.join(root, 'documents');
  const helperDirectory = 'node_modules/@deepseek-ai/libreoffice-kit-win32-x64/bin';
  const destination = path.join(runtime, helperDirectory);
  const source = path.join(root, 'VS', 'VC', 'Redist', 'MSVC', '14.44.35112', 'x64', 'Microsoft.VC143.CRT');
  mkdirSync(destination, { recursive: true }); mkdirSync(source, { recursive: true });
  writeFileSync(path.join(destination, 'libreoffice-kit.exe'), 'original helper');
  const files = {};
  for (const name of ['msvcp140.dll', 'vcruntime140.dll']) {
    const bytes = Buffer.from(`official fixture ${name}`);
    writeFileSync(path.join(source, name), bytes);
    files[name] = { bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
  }
  writeFileSync(path.join(runtime, 'windows-crt.lock.json'), JSON.stringify({ platform: 'win32-x64', helperDirectory, fileVersion: 'fixture', files }));
  return { root, runtime, destination, source, options: { runtime, platform: 'win32', arch: 'x64', sourceDirectories: [source] } };
}

test('stage deploys the entire locked CRT and verified packages no longer need Visual Studio', () => {
  const f = fixture();
  try {
    assert.equal(stageDocumentCrt(f.options).staged, true);
    assert.equal(readFileSync(path.join(f.destination, 'libreoffice-kit.exe'), 'utf8'), 'original helper');
    assert.equal(stageDocumentCrt({ ...f.options, checkOnly: true, sourceDirectories: [] }).files, 2);
    assert.equal(stageDocumentCrt({ ...f.options, sourceDirectories: [] }).staged, false);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test('missing or modified CRT fails check and repair restores only the locked runtime files', () => {
  const f = fixture();
  try {
    assert.throws(() => stageDocumentCrt({ ...f.options, checkOnly: true }), /missing or differs/);
    stageDocumentCrt(f.options);
    writeFileSync(path.join(f.destination, 'msvcp140.dll'), 'wrong version');
    assert.throws(() => stageDocumentCrt({ ...f.options, checkOnly: true }), /missing or differs/);
    assert.equal(stageDocumentCrt(f.options).staged, true);
    rmSync(path.join(f.destination, 'vcruntime140.dll'));
    assert.throws(() => stageDocumentCrt({ ...f.options, sourceDirectories: [] }), /official Visual Studio/);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test('staging rejects system directories, wrong versions and mismatched architecture', () => {
  const f = fixture();
  try {
    assert.throws(() => stageDocumentCrt({ ...f.options, sourceDirectories: [path.join(f.root, 'Windows', 'System32')] }), /Do not copy DLLs/);
    writeFileSync(path.join(f.source, 'msvcp140.dll'), 'unreviewed update');
    assert.throws(() => stageDocumentCrt(f.options), /official Visual Studio/);
    assert.throws(() => stageDocumentCrt({ ...f.options, arch: 'arm64' }), /not locked/);
    assert.deepEqual(stageDocumentCrt({ platform: 'linux' }), { required: false });
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test('verified CRT is first on the helper PATH for relocated long-path executables', async () => {
  const f = fixture();
  try {
    stageDocumentCrt(f.options);
    const env = { Path: 'C:\\Windows\\System32', OTHER: 'unchanged' };
    await configureNativeRuntime(f.runtime, env, 'win32');
    await configureNativeRuntime(f.runtime, env, 'win32');
    assert.equal(env.Path, `${f.destination};C:\\Windows\\System32`);
    assert.equal(env.OTHER, 'unchanged');
    writeFileSync(path.join(f.destination, 'msvcp140.dll'), 'invalid');
    const rejectedEnv = { PATH: 'original' };
    await assert.rejects(configureNativeRuntime(f.runtime, rejectedEnv, 'win32'), { code: 'DOCUMENT_NATIVE_RUNTIME_MISSING' });
    assert.equal(rejectedEnv.PATH, 'original');
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});
