import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createDocumentTool, projectPath } from '../resources/documents/index.mjs';

test('fixed host operations are scoped to the actual session project', async () => {
  const base = await mkdtemp(path.join(os.tmpdir(), 'omaax-doc-host-'));
  const root = path.join(base, 'project');
  await mkdir(root);
  await writeFile(path.join(root, 'report.docx'), 'fixture');
  const calls = [];
  const tool = createDocumentTool(() => ({ mode: 'workspace-write' }), async (...args) => { calls.push(args); return JSON.stringify({ ready: true }); });
  const exec = { agent: { session: { header: { cwd: root } } }, signal: new AbortController().signal };
  try {
    await tool.execute({ action: 'prepare' }, exec);
    await tool.execute({ action: 'pdf', input: 'report.docx', output: 'documents/report-v1.pdf' }, exec);
    assert.equal(calls.length, 2);
    assert.equal(calls[1][2].cwd, root);
    assert.ok(calls[1][1].includes(path.join(root, 'documents/report-v1.pdf')));
    for (const args of [
      { action: 'run', input: 'script.py' },
      { action: 'prepare', script: 'script.py' },
      { action: 'prepare', output: 'any' },
      { action: 'pdf', input: '../other.docx', output: 'new.pdf' },
      { action: 'pdf', input: 'report.docx', output: '../outside.pdf' },
      { action: 'pdf', input: 'report.docx', output: 'new.pdf', pages: '1' },
    ]) await assert.rejects(tool.execute(args, exec));
    assert.equal(calls.length, 2);
    await assert.rejects(tool.execute({ action: 'prepare' }, { agent: {} }), /local project/);
    const readonly = createDocumentTool(() => ({ mode: 'read-only' }), () => { throw Error('must not run'); });
    await assert.rejects(readonly.execute({ action: 'prepare' }, exec), /writable project/);
    const cancelled = new AbortController(); cancelled.abort();
    await assert.rejects(tool.execute({ action: 'prepare' }, { ...exec, signal: cancelled.signal }), /aborted/);
    assert.equal(calls.length, 2);
  } finally { await rm(base, { recursive: true, force: true }); }
});

test('document host refuses paths through project links leading outside', async () => {
  const base = await mkdtemp(path.join(os.tmpdir(), 'omaax-doc-links-'));
  const root = path.join(base, 'project'), outside = path.join(base, 'outside');
  await mkdir(root); await mkdir(outside);
  await writeFile(path.join(outside, 'report.docx'), 'outside');
  try {
    await symlink(outside, path.join(root, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
    await assert.rejects(projectPath(root, 'linked/report.docx', true), /outside/);
    await assert.rejects(projectPath(root, 'linked/new/report.pdf'), /outside/);
  } finally { await rm(path.join(root, 'linked'), { force: true }); await rm(base, { recursive: true, force: true }); }
});
