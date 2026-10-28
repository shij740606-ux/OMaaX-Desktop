import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import * as sourceGuard from '../dsh-plugins/auth-guard/index.js';

const desktop = fileURLToPath(new URL('..', import.meta.url));
const runtime = process.env.OMAAX_TEST_DSH_RUNTIME || path.join(desktop, 'dsh-runtime');
const runtimeRequire = createRequire(path.join(runtime, 'node_modules/@deepseek-ai/dsh/package.json'));
const load = name => import(pathToFileURL(runtimeRequire.resolve('@deepseek-ai/' + name)).href);
const [{ Context }, { default: SystemPrompt }, { default: Tools }] = await Promise.all([
  load('cordis'), load('dsh-system-prompt'), load('dsh-tools'),
]);
const bundledGuard = await import(pathToFileURL(runtimeRequire.resolve('@biodsh/auth-guard')).href);

for (const [label, { externalPolicy, createGuard }] of [['source', sourceGuard], ['bundled', bundledGuard]]) {
test(`actual SDK preserves external approval, cancellation, denial and account checks (${label})`, async () => {
  const ctx = new Context();
  let bodies = 0, loggedIn = true, kind = 'allow';
  try {
    await ctx.plugin(SystemPrompt, { includeHarnessIdentity: false });
    await ctx.plugin(Tools, { mode: 'native' });
    ctx.tools.register({
      name: 'mcp__papers__search', description: 'In-memory execution counter',
      parameters: { type: 'object', properties: {} },
      output: { schema: { type: 'object', properties: { ok: { type: 'boolean' } }, required: ['ok'] }, render: () => [{ type: 'text', text: 'fixture' }] },
      execute: async () => { bodies++; return { ok: true }; },
    });
    ctx.on('tools/pre-execute', externalPolicy(['papers'], {}).before);
    ctx.on('tools/pre-execute', async () => ({ kind, reason: 'Existing downstream policy' }));
    ctx.tools.guard(createGuard(() => ({ ok: loggedIn })));
    const run = () => ctx.tools.execute({ callId: 'probe', name: 'mcp__papers__search', arguments: {}, signal: new AbortController().signal });
    for (kind of ['ask', 'cancel', 'deny']) {
      assert.equal((await run()).isError, true, kind);
      assert.equal(bodies, 0, `${kind} must never execute the tool body`);
    }
    kind = 'allow';
    assert.equal((await run()).isError, false);
    assert.equal(bodies, 1);
    loggedIn = false;
    assert.equal((await run()).isError, true);
    assert.equal(bodies, 1, 'Explicit allow cannot bypass the account guard');
  } finally { await ctx.fiber.dispose(); }
});
}
