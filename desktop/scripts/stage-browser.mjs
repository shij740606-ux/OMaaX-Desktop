// Stage the pinned upstream MCP engine, never a browser binary or another DSH runtime.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../resources/browser');
const fingerprint = createHash('sha256').update(readFileSync(path.join(root, 'package-lock.json'))).digest('hex');
const marker = path.join(root, 'node_modules/.omaax-browser-lock');
function check() {
  for (const [name, version] of [['@playwright/mcp', '0.0.80'], ['playwright', '1.63.0-alpha-2026-08-31'], ['playwright-core', '1.63.0-alpha-2026-08-31']]) {
    const directory = path.join(root, 'node_modules', name);
    if (JSON.parse(readFileSync(path.join(directory, 'package.json'))).version !== version) throw Error(`Unexpected browser dependency: ${name}`);
    if (!existsSync(path.join(directory, 'LICENSE'))) throw Error(`Missing browser dependency license: ${name}`);
  }
  if (existsSync(path.join(root, 'node_modules/@deepseek-ai/dsh'))) throw Error('The browser runtime must not contain another DSH.');
  return true;
}
let ready = false;
if (process.argv.includes('--check')) { check(); ready = true; }
else if (existsSync(marker) && readFileSync(marker, 'utf8') === fingerprint) { try { ready = check(); } catch {} }
if (!ready) {
  const npm = [process.env.npm_execpath, path.join(path.dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js'),
    ...(process.env.PATH || '').split(path.delimiter).map(dir => path.join(dir, 'node_modules/npm/bin/npm-cli.js'))].find(file => file && existsSync(file));
  if (!npm) throw Error('Run browser staging through npm or Node with npm installed.');
  const env = { ...process.env, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: '1', npm_config_proxy: '', npm_config_https_proxy: '' };
  for (const name of Object.keys(env)) if (/^(https?_proxy|all_proxy)$/i.test(name)) delete env[name];
  const result = spawnSync(process.execPath, [npm, 'ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org', '--fetch-retries=1', '--fetch-timeout=60000'], { cwd: root, env, stdio: 'inherit', windowsHide: true });
  if (result.error) throw result.error;
  if (result.status !== 0) throw Error(`Browser resources could not be installed (${result.status}).`);
  check(); writeFileSync(marker, fingerprint);
}
console.log('Browser resources verified: Playwright MCP 0.0.80, installed system Edge/Chrome, stable DSH unchanged.');
