import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import test from 'node:test';
import { patchMcpTimeoutSource, assertMcpTimeoutSdkContract, verifyMcpTimeoutSdk, patchMcpTimeout, timeoutRuntime } from './patch-dsh-mcp-timeout.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(desktop, 'package.json'));
const sdkRoot = path.resolve(path.dirname(require.resolve('@modelcontextprotocol/sdk/client/index.js')), '../../esm');
const { Client } = await import(pathToFileURL(path.join(sdkRoot, 'client/index.js')));
const { Server } = await import(pathToFileURL(path.join(sdkRoot, 'server/index.js')));
const { InMemoryTransport } = await import(pathToFileURL(path.join(sdkRoot, 'inMemory.js')));
const { CallToolRequestSchema, CallToolResultSchema } = await import(pathToFileURL(path.join(sdkRoot, 'types.js')));
const protocol = readFileSync(path.join(sdkRoot, 'shared/protocol.js'), 'utf8');
const maxDelay = 2147483647;
const compile = (timers = { setTimeout, performance }) => new Function('Client', 'MAX_TIMER_DELAY_MS', 'setTimeout', 'performance', `${timeoutRuntime}\nreturn { OmaaxSshMcpClient, omaaxToolCallTimeout };`)(Client, maxDelay, timers.setTimeout, timers.performance);
const { OmaaxSshMcpClient, omaaxToolCallTimeout } = compile();

test('SSH deadlines follow valid explicit values and existing 900-second default', () => {
  const opts = { serverName: 'biodsh_ssh', toolCallTimeoutMs: 1800000 };
  for (const name of ['execute', 'server_environment']) {
    for (const [seconds, expected] of [[0, 0], [1, 61000], [180, 240000], [900, 960000], [3000000, 3000060000], [undefined, 960000], [-1, 960000], ['300', 960000], [1.5, 960000]]) {
      assert.equal(omaaxToolCallTimeout(name, { timeoutSeconds: seconds }, opts), expected);
    }
  }
  assert.equal(omaaxToolCallTimeout('workflow_prepare', {}, opts), 1800000);
  assert.equal(omaaxToolCallTimeout('workflow_prepare', {}, { serverName: 'biodsh_ssh' }), 1800000);
});

test('external MCP keeps its configured timeout regardless of similarly named arguments', () => {
  for (const value of [undefined, 125, 60000]) {
    assert.equal(omaaxToolCallTimeout('execute', { timeoutSeconds: 0 }, { serverName: 'external', toolCallTimeoutMs: value }), value);
  }
});

test('SDK cleanup adapter is guarded against dependency drift', () => {
  assert.doesNotThrow(() => assertMcpTimeoutSdkContract(protocol));
  for (const token of ['_setupTimeout(messageId, timeout, maxTotalTimeout, onTimeout, resetTimeoutOnProgress = false)', 'clearTimeout(info.timeoutId);', 'this._timeoutInfo.clear();']) {
    assert.throws(() => assertMcpTimeoutSdkContract(protocol.replaceAll(token, 'changed')), /contract changed/);
  }
});

const sourceFixture = '/** Call without the SDK pre-validating an output schema the bridge may not support. */\n\t\ttimeout: opts.toolCallTimeoutMs\n\t\tconst generation = new Client({';
test('patch is idempotent and rejects partial or changed bridge source', () => {
  const patched = patchMcpTimeoutSource(sourceFixture);
  assert.equal(patchMcpTimeoutSource(patched), patched);
  assert.throws(() => patchMcpTimeoutSource(sourceFixture.replace('opts.toolCallTimeoutMs', 'opts.other')), /anchor changed/);
  assert.throws(() => patchMcpTimeoutSource(patched.replace('seconds * 1000', 'seconds * 2000')), /incomplete or changed/);
});

test('relative runtime paths resolve before loading SDK contract', () => {
  const fixture = mkdtempSync(path.join(desktop, '.mcp-timeout-test-'));
  try {
    const plugin = path.join(fixture, 'node_modules/@deepseek-ai/dsh-mcp-client/lib/index.js');
    mkdirSync(path.dirname(plugin), { recursive: true });
    writeFileSync(plugin, sourceFixture);
    const sdk = path.join(fixture, 'node_modules/@modelcontextprotocol/sdk');
    mkdirSync(sdk, { recursive: true });
    writeFileSync(path.join(sdk, 'package.json'), JSON.stringify({ name: '@modelcontextprotocol/sdk', exports: { './client/index.js': './dist/cjs/client/index.js' } }));
    for (const flavor of ['cjs', 'esm']) {
      mkdirSync(path.join(sdk, 'dist', flavor, 'client'), { recursive: true });
      mkdirSync(path.join(sdk, 'dist', flavor, 'shared'), { recursive: true });
      writeFileSync(path.join(sdk, 'dist', flavor, 'client/index.js'), '');
      writeFileSync(path.join(sdk, 'dist', flavor, 'shared/protocol.js'), protocol);
    }
    assert.deepEqual(patchMcpTimeout(path.relative(process.cwd(), fixture)), [plugin]);
    writeFileSync(path.join(sdk, 'dist/esm/shared/protocol.js'), 'changed');
    assert.throws(() => verifyMcpTimeoutSdk(plugin), /contract changed/);
  } finally { rmSync(fixture, { recursive: true, force: true }); }
});

async function connected(ClientType = OmaaxSshMcpClient, handler) {
  const calls = [];
  const client = new ClientType({ name: 'timeout-test', version: '1' }, { capabilities: {} });
  const server = new Server({ name: 'timeout-fixture', version: '1' }, { capabilities: { tools: {} } });
  server.setRequestHandler(CallToolRequestSchema, async (request, extra) => {
    calls.push(request.params);
    return handler ? handler(request, extra) : new Promise(() => {});
  });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  return { client, server, calls, call: (timeout, signal) => client.request({ method: 'tools/call', params: { name: 'execute', arguments: {} } }, CallToolResultSchema, { timeout, signal }) };
}

test('normal finite requests retain the real SDK timeout and submit once', async () => {
  const fixture = await connected();
  try {
    await assert.rejects(fixture.call(15), error => error.code === -32001);
    assert.equal(fixture.calls.length, 1);
    assert.equal(fixture.client._timeoutInfo.size, 0);
    assert.equal(fixture.client._responseHandlers.size, 0);
  } finally { await fixture.client.close(); }
});

test('zero permits completion and retains no timer', async () => {
  const fixture = await connected(undefined, async () => {
    await new Promise(resolve => setTimeout(resolve, 20));
    return { content: [{ type: 'text', text: 'completed' }] };
  });
  try {
    const pending = fixture.call(0);
    assert.equal(fixture.client._timeoutInfo.size, 0);
    assert.equal((await pending).content[0].text, 'completed');
    assert.equal(fixture.calls.length, 1);
    assert.equal(fixture.client._responseHandlers.size, 0);
  } finally { await fixture.client.close(); }
});

for (const timeout of [0, maxDelay + 5000]) {
  test(`timeout ${timeout} supports user cancellation without reissuing`, async () => {
    const fixture = await connected();
    try {
      const controller = new AbortController();
      const pending = fixture.call(timeout, controller.signal);
      const handle = [...fixture.client._timeoutInfo.values()][0]?.timeoutId;
      controller.abort(new Error('user cancelled'));
      await assert.rejects(pending, /user cancelled/);
      assert.equal(fixture.calls.length, 1);
      assert.equal(fixture.client._timeoutInfo.size, 0);
      assert.equal(fixture.client._responseHandlers.size, 0);
      if (handle) assert.equal(handle._destroyed, true);
    } finally { await fixture.client.close(); }
  });
  test(`timeout ${timeout} is cleaned on transport disconnect`, async () => {
    const fixture = await connected();
    const pending = fixture.call(timeout);
    const rejected = assert.rejects(pending, error => error.code === -32000);
    const handle = [...fixture.client._timeoutInfo.values()][0]?.timeoutId;
    await fixture.client.close();
    await rejected;
    assert.equal(fixture.calls.length, 1);
    assert.equal(fixture.client._timeoutInfo.size, 0);
    assert.equal(fixture.client._responseHandlers.size, 0);
    if (handle) assert.equal(handle._destroyed, true);
  });
}

test('long deadlines use bounded timer chunks, then the SDK timeout path and cleanup', async () => {
  let now = 0;
  const chunks = [];
  const adapted = compile({ performance: { now: () => now }, setTimeout: (callback, delay) => {
    const handle = setTimeout(callback, delay);
    chunks.push({ callback, delay, handle });
    return handle;
  } });
  const fixture = await connected(adapted.OmaaxSshMcpClient);
  try {
    const pending = fixture.call(maxDelay + 1000);
    const rejected = assert.rejects(pending, error => error.code === -32001);
    assert.equal(chunks.length, 1);
    assert.equal(chunks[0].delay, maxDelay);
    clearTimeout(chunks[0].handle);
    now = maxDelay;
    chunks[0].callback();
    assert.equal(chunks.length, 2);
    assert.equal(chunks[1].delay, 1000);
    clearTimeout(chunks[1].handle);
    now += 1000;
    chunks[1].callback();
    await rejected;
    assert.equal(fixture.calls.length, 1);
    assert.equal(fixture.client._timeoutInfo.size, 0);
    assert.equal(fixture.client._responseHandlers.size, 0);
  } finally { await fixture.client.close(); }
});

test('successful long request clears its pending chunk', async () => {
  const fixture = await connected(undefined, async () => {
    await new Promise(resolve => setTimeout(resolve, 10));
    return { content: [] };
  });
  try {
    const pending = fixture.call(maxDelay + 1000);
    const handle = [...fixture.client._timeoutInfo.values()][0].timeoutId;
    await pending;
    assert.equal(handle._destroyed, true);
    assert.equal(fixture.client._timeoutInfo.size, 0);
  } finally { await fixture.client.close(); }
});
