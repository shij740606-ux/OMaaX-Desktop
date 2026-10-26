import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runtime = path.join(desktop, 'dsh-runtime/node_modules/@deepseek-ai/dsh');
const runtimeRequire = createRequire(path.join(runtime, 'package.json'));
const { FileSystemSkillProvider } = await import(pathToFileURL(runtimeRequire.resolve('@deepseek-ai/dsh-skill-filesystem')).href);
const bundled = path.join(desktop, 'resources/documents/skills');

async function profile(callback) {
  const root = await mkdtemp(path.join(tmpdir(), 'omaax-document-skills-'));
  const home = path.join(root, 'dsh-home');
  const skills = path.join(home, 'skills');
  const workspace = path.join(root, 'project');
  await mkdir(skills, { recursive: true });
  await mkdir(path.join(workspace, '.git'), { recursive: true });
  const warnings = [];
  const controller = new AbortController();
  const provider = new FileSystemSkillProvider({ get: () => undefined, logger: { warn: value => warnings.push(value) } },
    { signal: controller.signal, invalidate() {} },
    { dshHome: home, agentsHome: path.join(root, 'agents'), customSkillDirs: [skills], bundledSkillDir: bundled, watch: false });
  try { await callback({ root, home, skills, workspace, provider, warnings }); }
  finally {
    controller.abort();
    await provider.dispose();
    // Only the mkdtemp-generated isolated fixture is removed.
    assert.ok(path.resolve(root).startsWith(path.resolve(tmpdir()) + path.sep));
    assert.ok(path.basename(root).startsWith('omaax-document-skills-'));
    await rm(root, { recursive: true, force: true });
  }
}

test('53 stable Harness discovers and loads document skill for a fresh account', async () => {
  assert.equal(JSON.parse(await readFile(path.join(runtime, 'package.json'), 'utf8')).version, '0.1.5-rc.1');
  await profile(async ({ provider, workspace, warnings }) => {
    const candidates = await provider.list({ cwd: workspace });
    assert.ok(Array.isArray(candidates));
    assert.deepEqual(candidates.map(skill => skill.name), ['omaax-documents']);
    const doc = await provider.get(candidates[0], { cwd: workspace, signal: new AbortController().signal });
    assert.equal(doc.source, 'bundled');
    assert.equal(doc.invocation.modelInvocable, true);
    assert.equal(doc.resourceBase.path, path.join(bundled, 'omaax-documents'));
    assert.match(doc.content, /omaax_documents/);
    assert.match(doc.content, /python-docx/);
    assert.match(doc.content, /python-pptx/);
    assert.match(doc.content, /pypdf/);
    assert.deepEqual(warnings, []);
  });
});

test('existing user skills and documents remain readable alongside the default document skill', async () => {
  await profile(async ({ provider, workspace, home, skills, warnings }) => {
    const oldSkill = '---\nname: existing-research\ndescription: Existing user research guide.\n---\nKeep this guide unchanged.\n';
    const settings = 'locale:\n  preference: en\n';
    const oldHistory = '{"message":"Previous conversation"}\n';
    await mkdir(path.join(skills, 'existing-research'));
    await writeFile(path.join(skills, 'existing-research/SKILL.md'), oldSkill);
    await writeFile(path.join(home, 'settings.yaml'), settings);
    await writeFile(path.join(home, 'history.fixture'), oldHistory);
    const candidates = await provider.list({ cwd: workspace });
    const names = new Set(candidates.map(skill => skill.name));
    assert.deepEqual(names, new Set(['existing-research', 'omaax-documents']));
    const previous = await provider.get(candidates.find(skill => skill.name === 'existing-research'), { cwd: workspace });
    assert.match(previous.content, /Keep this guide unchanged/);
    const document = await provider.get(candidates.find(skill => skill.name === 'omaax-documents'), { cwd: workspace });
    assert.match(document.content, /new revision/);
    assert.equal(await readFile(path.join(skills, 'existing-research/SKILL.md'), 'utf8'), oldSkill);
    assert.equal(await readFile(path.join(home, 'settings.yaml'), 'utf8'), settings);
    assert.equal(await readFile(path.join(home, 'history.fixture'), 'utf8'), oldHistory);
    assert.deepEqual(warnings, []);
  });
});
