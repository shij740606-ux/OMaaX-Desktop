import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { nsisExecutableBytes, payloadManifest } from './installer-payload.mjs';

test('hashes the shipped NSIS executable and every resource, excluding only its own manifest', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'omaax-payload-'));
  try {
    writeFileSync(path.join(dir, 'main.exe'), 'start__TAURI_BUNDLE_TYPE_VAR_UNKend');
    mkdirSync(path.join(dir, 'resources'));
    writeFileSync(path.join(dir, 'resources/a.txt'), 'payload');
    const manifest = payloadManifest({ version: '0.4.50', configDir: dir, executable: path.join(dir, 'main.exe'), resources: {
      resources: 'scripts', missing: 'installation/payload-manifest.json',
    } });
    assert.equal(manifest.files.length, 2);
    assert.equal(manifest.files.find(f => f.path === 'OMaaX.exe').sha256,
      createHash('sha256').update('start__TAURI_BUNDLE_TYPE_VAR_NSSend').digest('hex'));
    assert.equal(manifest.files.find(f => f.path === 'scripts/a.txt').bytes, 7);
    for (const resources of [{ resources: '../escape' }, { resources: 'SCRIPTS', 'resources/a.txt': 'scripts/a.txt' }]) {
      assert.throws(() => payloadManifest({ version: 'x', configDir: dir, executable: path.join(dir, 'main.exe'), resources }));
    }
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('unexpected or repeated executable markers fail the build', () => {
  assert.throws(() => nsisExecutableBytes(Buffer.from('no marker')));
  assert.throws(() => nsisExecutableBytes(Buffer.from('__TAURI_BUNDLE_TYPE_VAR_UNK__TAURI_BUNDLE_TYPE_VAR_UNK')));
});
