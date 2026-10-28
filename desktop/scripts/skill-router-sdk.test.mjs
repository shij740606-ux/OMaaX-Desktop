import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import * as router from '../dsh-plugins/skill-router/index.js';

const desktop = fileURLToPath(new URL('..', import.meta.url));
const runtimeRequire = createRequire(path.join(desktop, 'dsh-runtime/node_modules/@deepseek-ai/dsh/package.json'));
const load = name => import(pathToFileURL(runtimeRequire.resolve('@deepseek-ai/' + name)).href);
const [{ Context }, { default: Skills }, { agentEvents }] = await Promise.all([load('cordis'), load('dsh-skill'), load('dsh-agent')]);

test('actual registry keeps per-agent ranking and can load a skill outside the capped catalog', async () => {
  const ctx = new Context();
  const saved = process.env.BIODSH_SKILL_CATALOG_MAX;
  process.env.BIODSH_SKILL_CATALOG_MAX = '1';
  try {
    await ctx.plugin(Skills);
    for (const name of ['rice-analysis', 'apple-analysis', 'other-analysis']) ctx.skills.register({
      name, source: 'bundled', description: name, content: `Instructions for ${name}`,
    });
    await ctx.plugin(router);
    const first = {}, second = {};
    const query = (agent, text) => agentEvents(ctx, agent).waterfall('agent/pre-step', {
      messages: [{ role: 'user', source: { kind: 'user' }, content: [{ type: 'text', text }] }],
    }, async () => ({ kind: 'enter', messages: [] }));
    await query(first, 'rice');
    await query(second, 'apple');
    assert.deepEqual((await ctx.skills.snapshot({ scope: first })).skills.map(skill => skill.name), ['rice-analysis']);
    assert.deepEqual((await ctx.skills.snapshot({ scope: second })).skills.map(skill => skill.name), ['apple-analysis']);
    assert.match((await ctx.skills.get('other-analysis', { scope: first })).content, /Instructions for other-analysis/);
  } finally {
    await ctx.fiber.dispose();
    if (saved === undefined) delete process.env.BIODSH_SKILL_CATALOG_MAX;
    else process.env.BIODSH_SKILL_CATALOG_MAX = saved;
  }
});
