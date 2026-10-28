import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, mkdir, symlink, readdir } from 'node:fs/promises';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { outputDirectory, inside, validateOutput } from '../resources/browser/index.mjs';
import { launchArguments, browserExecutable } from '../resources/browser/launcher.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = await mkdtemp(path.join(desktop, '../validation-local/browser-'));
const runtimeRequire = createRequire(path.join(desktop, 'dsh-runtime/node_modules/@deepseek-ai/dsh/package.json'));
const modules = process.env.OMAAX_BROWSER_TEST_DSH_MODULES || path.resolve(path.dirname(runtimeRequire.resolve('@deepseek-ai/dsh-tools')), '../../..');
process.env.OMAAX_DSH_MODULES = modules;
process.env.OMAAX_BROWSER_HOME = path.join(root, 'audit');
const selectedRequire = createRequire(path.join(modules, 'omaax-test-runtime.cjs'));
const load = name => import(pathToFileURL(selectedRequire.resolve('@deepseek-ai/' + name)).href);

test('launch uses system browser, isolated state and no personal browser attachment', () => {
  const args = launchArguments(root, true);
  assert.ok(args.includes('--isolated'));
  assert.ok(args.includes('--headless'));
  assert.ok(args.includes(browserExecutable()));
  for (const flag of ['--cdp-endpoint', '--extension', '--storage-state', '--user-data-dir', '--allow-unrestricted-file-access', '--no-sandbox']) assert.ok(!args.includes(flag));
  assert.throws(() => launchArguments('../outside'), /absolute/);
  assert.ok(!inside(root, path.dirname(root)));
});

test('project output rejects a linked results folder', async () => {
  const project = path.join(root, 'linked-project');
  const outside = path.join(root, 'outside');
  await mkdir(project); await mkdir(outside);
  await symlink(outside, path.join(project, 'browser-results'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(outputDirectory(project, 'test'), /linked/);
  assert.deepEqual(await readdir(outside), []);
});

test('bundled DSH discovers and runs official browser tools with isolated sessions', { timeout: 120000 }, async () => {
  const [{ Context, Service }, { default: SystemPrompt }, { default: Tools }, { createScope, bindScopeParent }] = await Promise.all([
    load('cordis'), load('dsh-system-prompt'), load('dsh-tools'), load('dsh-scope'),
  ]);
  const ctx = new Context();
  const browser = await import(pathToFileURL(process.env.OMAAX_BROWSER_TEST_ENTRY || path.join(desktop, process.env.OMAAX_BROWSER_TEST_PROTECTED ? 'protected-resources/browser/index.mjs' : 'resources/browser/index.mjs')).href);
  let slowReached;
  const slow = new Promise(resolve => slowReached = resolve);
  const server = createServer((request, response) => {
    if (request.url === '/slow') { slowReached(); return; }
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    response.end('<!doctype html><html><head><title>OMaaX browser test</title></head><body><h1>Research portal</h1><label>Topic <input aria-label="Topic"></label><button onclick="document.querySelector(\'output\').textContent=document.querySelector(\'input\').value;localStorage.setItem(\'task\',\'saved\')">Save topic</button><output aria-live="polite">No topic yet</output></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const scopes = [];
  const agent = async id => {
    const project = path.join(root, id); await mkdir(project);
    const value = { id, session: { header: { cwd: project } } };
    const scope = createScope(ctx, value); value.ctx = scope.ctx; scopes.push(scope);
    return value;
  };
  const invoke = async (owner, name, args) => {
    const result = await ctx.tools.execute({ callId: `test-${Math.random()}`, name, arguments: args, agent: owner, signal: AbortSignal.timeout(90000) });
    assert.equal(result.isError, false, JSON.stringify(result));
    return result;
  };
  const text = result => result.content.filter(item => item.type === 'text').map(item => item.text).join('\n');
  try {
    await ctx.plugin(SystemPrompt, { includeHarnessIdentity: false });
    await ctx.plugin(Tools, { mode: 'native' });
    await ctx.plugin(class Sandbox extends Service {
      constructor(child) { super(child, 'sandboxPolicy'); }
      async resolve() { return { mode: 'workspace-write' }; }
    });
    await ctx.plugin(browser, { headless: true });
    const first = await agent('first'), second = await agent('second');
    assert.deepEqual(ctx.tools.schemas(first).filter(item => item.name.startsWith(browser.prefix)), []);
    const [started, concurrent] = await Promise.all([invoke(first, 'omaax_browser', { action: 'start' }), invoke(first, 'omaax_browser', { action: 'start' })]);
    assert.match(text(started), /browser_navigate/);
    assert.deepEqual(JSON.parse(text(started)), JSON.parse(text(concurrent)));
    assert.ok(ctx.tools.schemas(first).some(item => item.name === `${browser.prefix}browser_click`));
    assert.ok(!ctx.tools.schemas(second).some(item => item.name.startsWith(browser.prefix)));
    await invoke(first, `${browser.prefix}browser_navigate`, { url });
    const snapshot = text(await invoke(first, `${browser.prefix}browser_snapshot`, {}));
    const input = snapshot.match(/textbox "Topic" \[ref=([^\]]+)\]/)?.[1];
    const button = snapshot.match(/button "Save topic" \[ref=([^\]]+)\]/)?.[1];
    assert.ok(input && button, snapshot);
    await invoke(first, `${browser.prefix}browser_type`, { element: 'Topic', target: input, text: 'Rice salt tolerance' });
    await invoke(first, `${browser.prefix}browser_click`, { element: 'Save topic', target: button });
    assert.match(text(await invoke(first, `${browser.prefix}browser_snapshot`, {})), /Rice salt tolerance/);
    const browserState = JSON.parse(text(started));
    const image = await invoke(first, `${browser.prefix}browser_take_screenshot`, { type: 'png', filename: path.join(browserState.output, 'browser-proof.png'), fullPage: true, scale: 'css' });
    assert.match(text(image), /browser-proof/);
    const files = await readdir(path.join(root, 'first/browser-results'), { recursive: true });
    const shot = files.find(name => name.endsWith('browser-proof.png'));
    assert.ok(shot, JSON.stringify(files));
    assert.equal((await readFile(path.join(root, 'first/browser-results', shot))).subarray(1, 4).toString(), 'PNG');
    await assert.rejects(validateOutput({ root: path.join(root, 'first'), output: browserState.output }, path.join(browserState.output, 'browser-proof.png')), /existing/);
    await assert.rejects(validateOutput({ root: path.join(root, 'first'), output: browserState.output }, '../outside.txt'), /Save browser/);
    const concurrentFile = path.join(browserState.output, 'concurrent.png');
    const screenshots = await Promise.all([1, 2].map(index => ctx.tools.execute({
      callId: `concurrent-screenshot-${index}`, name: `${browser.prefix}browser_take_screenshot`,
      arguments: { type: 'png', filename: concurrentFile, scale: 'css' }, agent: first, signal: AbortSignal.timeout(20000),
    })));
    assert.equal(screenshots.filter(result => !result.isError).length, 1, 'Only the first queued screenshot may create the same filename');
    assert.match(text(screenshots.find(result => result.isError)), /existing browser files/);
    await invoke(second, 'omaax_browser', { action: 'start' });
    await invoke(second, `${browser.prefix}browser_navigate`, { url });
    assert.match(text(await invoke(second, `${browser.prefix}browser_snapshot`, {})), /No topic yet/);
    const state = await invoke(second, `${browser.prefix}browser_evaluate`, { function: '() => localStorage.getItem("task")' });
    assert.match(text(state), /null/);
    const child = await agent('child'); bindScopeParent(child, first);
    const denied = await ctx.tools.execute({ callId: 'cross-session', name: `${browser.prefix}browser_snapshot`, arguments: {}, agent: child, signal: new AbortController().signal });
    assert.equal(denied.isError, true);
    await invoke(first, 'omaax_browser', { action: 'close' });
    assert.ok(!ctx.tools.schemas(first).some(item => item.name.startsWith(browser.prefix)));
    assert.match(text(await invoke(second, `${browser.prefix}browser_snapshot`, {})), /No topic yet/);
    const navigating = ctx.tools.execute({ callId: 'slow-navigation', name: `${browser.prefix}browser_navigate`, arguments: { url: `${url}/slow` }, agent: second, signal: AbortSignal.timeout(20000) });
    await slow;
    const closingAt = Date.now();
    await invoke(second, 'omaax_browser', { action: 'close' });
    assert.ok(Date.now() - closingAt < 5000, 'Closing must cancel slow navigation instead of waiting 60 seconds');
    assert.equal((await navigating).isError, true);
    await invoke(second, 'omaax_browser', { action: 'start' });
    await invoke(second, `${browser.prefix}browser_navigate`, { url });
    await scopes[1].dispose();
    assert.ok(!ctx.tools.schemas(second).some(item => item.name.startsWith(browser.prefix)));
    const audit = await readFile(path.join(root, 'audit/audit.jsonl'), 'utf8');
    assert.match(audit, /browser_click/); assert.ok(!audit.includes('Rice salt tolerance'));
    console.log(`Browser proof: ${path.join(root, 'first/browser-results', shot)}`);
  } catch (error) { console.error(error); throw error; } finally {
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    await Promise.all(scopes.map(scope => scope.dispose()));
    await ctx.fiber.dispose();
  }
});
