// Exercise packaged components on a clean macOS runner using synthetic data only.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import path from 'node:path';
import os from 'node:os';

assert.equal(process.platform, 'darwin', 'Run package acceptance on macOS');
const app = path.resolve(process.argv[2]);
const resources = path.join(app, 'Contents/Resources');
const node = path.join(resources, 'node/bin/node');
const key = readFileSync('protected-resources/local-code.key', 'utf8').trim();
assert.match(key, /^[a-f0-9]{64}$/);
assert.ok(readFileSync(path.join(app, 'Contents/MacOS/OMaaX')).includes(Buffer.from(key)));
assert.ok(!existsSync(path.join(resources, 'local-code.key')));
assert.match(readFileSync(path.join(resources, 'documents/cli.mjs'), 'utf8'), /OMaaX protected local module/);
const state = mkdtempSync(path.join(os.tmpdir(), 'omaax-macos-package-'));
const env = { ...process.env, OMAAX_LOCAL_CODE_KEY: key };
const common = { state, lang: 'zh', uv: path.join(resources, 'bin/uv'), documents: path.join(resources, 'documents') };
function invoke(action, input = {}) {
  const result = spawnSync(node, [path.join(resources, 'scripts/figure-pdf-runtime.mjs')], {
    env, encoding: 'utf8', timeout: 660_000, maxBuffer: 65_000_000,
    input: JSON.stringify({ ...common, action, ...input }),
  });
  assert.equal(result.status, 0, `Packaged PDF ${action} failed`);
  const output = JSON.parse(result.stdout);
  assert.ok(!output.error, `Packaged PDF ${action} returned an error`);
  return output;
}
const fonts = invoke('fonts');
assert.ok(fonts.families.length > 0, 'macOS system fonts must be discoverable');
const prepared = invoke('prepare');
assert.equal(prepared.ready, true);
assert.equal(prepared.runtimeVersion, '1.25.5');
const fixture = spawnSync(path.join(state, 'runtime/bin/python'), ['-I', '-B', '-c',
  'import pymupdf,base64; d=pymupdf.open(); p=d.new_page(); p.insert_text((30,30),"Measured 12.34"); print(base64.b64encode(d.tobytes()).decode())'],
  { encoding: 'utf8', timeout: 30_000 });
assert.equal(fixture.status, 0, 'Synthetic PDF creation failed');
const inspected = invoke('inspect', { name: '科研结果.pdf', dataUrl: 'data:application/pdf;base64,' + fixture.stdout.trim() });
assert.equal(inspected.totalPages, 1);
assert.ok(inspected.pages[0].preview.startsWith('data:image/png;base64,'));
const editable = invoke('import', { token: inspected.token, pages: [1], mode: 'editable' });
assert.match(editable.items[0].svg, /<text\b/);
assert.ok(editable.items[0].svg.includes('Measured 12.34'));
assert.ok(editable.items[0].name.includes('科研结果.pdf'));
const whole = invoke('import', { token: inspected.token, pages: [1], mode: 'page' });
assert.ok(whole.items[0].dataUrl.startsWith('data:image/png;base64,'));
const report = {
  status: 'passed', architecture: process.arch,
  executableSha256: createHash('sha256').update(readFileSync(path.join(app, 'Contents/MacOS/OMaaX'))).digest('hex'),
  fontFamilyCount: fonts.families.length, freshPdfEnvironment: true,
  encryptedDocumentHelperLoaded: true, pdfEditableImport: true, pdfWholePageImport: true,
  chineseFilenamePreserved: true, noUserData: true, noModelRequest: true,
  nativeUiTested: false, notarizationVerified: false,
};
writeFileSync('macos-feature-verification.json', JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report));
