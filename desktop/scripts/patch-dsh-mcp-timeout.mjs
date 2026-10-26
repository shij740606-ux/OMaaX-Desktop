// Align the built-in SSH bridge with its existing command deadlines. External
// MCP services and SDK initialization/listing keep their original deadlines.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const marker = 'OMAAX_MCP_TOOL_TIMEOUT_V1';

// This is deliberately local to the built-in SSH Client. The SDK still owns
// request cancellation, timeout errors, response cleanup and connection cleanup.
export const timeoutRuntime = `/*OMAAX_MCP_TOOL_TIMEOUT_V1*/
function omaaxToolCallTimeout(rawName, args, opts) {
  if (opts.serverName !== "biodsh_ssh") return opts.toolCallTimeoutMs;
  if (rawName !== "execute" && rawName !== "server_environment") return opts.toolCallTimeoutMs ?? 1800000;
  const requested = args?.timeoutSeconds;
  const seconds = Number.isSafeInteger(requested) && requested >= 0 ? requested : 900;
  return seconds === 0 ? 0 : seconds * 1000 + 60000;
}
class OmaaxSshMcpClient extends Client {
  _setupTimeout(messageId, timeout, maxTotalTimeout, onTimeout, resetTimeoutOnProgress = false) {
    // SSH already defines zero as waiting until completion. Never pass it to
    // the SDK's setTimeout, where zero would mean an immediate timeout.
    if (timeout === 0) return;
    if (timeout <= MAX_TIMER_DELAY_MS) {
      return super._setupTimeout(messageId, timeout, maxTotalTimeout, onTimeout, resetTimeoutOnProgress);
    }
    // Node otherwise overflows a long timeout to 1 ms. Keep the active chunk
    // in the SDK's own map so response, cancellation and disconnect all clear it.
    const started = performance.now();
    const info = { timeoutId: undefined, startTime: Date.now(), timeout, maxTotalTimeout, resetTimeoutOnProgress: false, onTimeout };
    const tick = () => {
      if (this._timeoutInfo.get(messageId) !== info) return;
      const remaining = timeout - (performance.now() - started);
      if (remaining <= 0) { onTimeout(); return; }
      info.timeoutId = setTimeout(tick, Math.min(MAX_TIMER_DELAY_MS, remaining));
    };
    this._timeoutInfo.set(messageId, info);
    tick();
  }
}
`;

const anchors = [
  ['/** Call without the SDK pre-validating an output schema the bridge may not support. */', `${timeoutRuntime}\n/** Call without the SDK pre-validating an output schema the bridge may not support. */`],
  ['\t\ttimeout: opts.toolCallTimeoutMs\n', '\t\ttimeout: omaaxToolCallTimeout(rawName, args, opts)\n'],
  ['\t\tconst generation = new Client({', '\t\tconst generation = new (config.serverName === "biodsh_ssh" ? OmaaxSshMcpClient : Client)({'],
];

export function patchMcpTimeoutSource(input) {
  const source = input.replace(/\r\n/g, '\n');
  if (source.includes(marker)) {
    if (!source.includes(timeoutRuntime) || !anchors.every(([, after]) => source.includes(after))) {
      throw new Error('DSH MCP timeout patch is incomplete or changed');
    }
    return input;
  }
  for (const [before] of anchors) {
    if (source.split(before).length !== 2) throw new Error(`DSH MCP timeout patch anchor changed: ${before}`);
  }
  return anchors.reduce((text, [before, after]) => text.replace(before, after), source);
}

export function assertMcpTimeoutSdkContract(input) {
  const source = input.replace(/\r\n/g, '\n');
  const contracts = [
    '_setupTimeout(messageId, timeout, maxTotalTimeout, onTimeout, resetTimeoutOnProgress = false)',
    'this._timeoutInfo.set(messageId, {\n            timeoutId: setTimeout(onTimeout, timeout),',
    '_cleanupTimeout(messageId) {\n        const info = this._timeoutInfo.get(messageId);\n        if (info) {\n            clearTimeout(info.timeoutId);\n            this._timeoutInfo.delete(messageId);',
    'for (const info of this._timeoutInfo.values()) {\n            clearTimeout(info.timeoutId);\n        }\n        this._timeoutInfo.clear();',
    'this._setupTimeout(messageId, timeout, options?.maxTotalTimeout, timeoutHandler, options?.resetTimeoutOnProgress ?? false);',
    'const cancel = (reason) => {\n                this._responseHandlers.delete(messageId);\n                this._progressHandlers.delete(messageId);\n                this._cleanupTimeout(messageId);',
    'this._responseHandlers.delete(messageId);\n        this._cleanupTimeout(messageId);',
  ];
  if (contracts.some(value => !source.includes(value))) throw new Error('MCP SDK timeout/cleanup contract changed; review the local SSH timeout adapter before staging');
}

export function verifyMcpTimeoutSdk(file) {
  const require = createRequire(path.resolve(file));
  const client = require.resolve('@modelcontextprotocol/sdk/client/index.js');
  const distribution = path.resolve(path.dirname(client), '../..');
  // require.resolve selects CJS, while the patched DSH plugin imports ESM.
  // Check both shipped implementations; a layout change must be reviewed.
  if (path.basename(distribution) !== 'dist') throw new Error('MCP SDK distribution layout changed');
  for (const flavor of ['cjs', 'esm']) {
    assertMcpTimeoutSdkContract(readFileSync(path.join(distribution, flavor, 'shared/protocol.js'), 'utf8'));
  }
}

export function patchMcpTimeout(runtimeDir = path.join(root, 'dsh-runtime')) {
  runtimeDir = path.resolve(runtimeDir);
  const candidates = [
    'node_modules/@deepseek-ai/dsh-mcp-client/lib/index.js',
    'node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-mcp-client/lib/index.js',
  ].map(file => path.join(runtimeDir, file)).filter(existsSync);
  if (!candidates.length) throw new Error('DSH MCP runtime missing; cannot stage SSH timeout alignment');
  // Validate every target before changing any file. Dependency updates fail closed.
  const changes = candidates.map(file => {
    verifyMcpTimeoutSdk(file);
    return [file, patchMcpTimeoutSource(readFileSync(file, 'utf8'))];
  });
  for (const [file, source] of changes) writeFileSync(file, source);
  return candidates;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  console.log(`DSH SSH timeout alignment ready (${patchMcpTimeout(process.argv[2]).length} runtime)`);
}
