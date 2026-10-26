// Install only the locked document converter, separate from the stable DSH tree.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { stageDocumentCrt } from './stage-document-crt.mjs';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runtime = path.join(desktop, 'resources', 'documents');
const manifest = JSON.parse(readFileSync(path.join(runtime, 'package.json'), 'utf8'));
const version = manifest.dependencies['@deepseek-ai/libreoffice-kit'];
const fingerprint = createHash('sha256').update(readFileSync(path.join(runtime, 'package-lock.json'))).digest('hex');
const marker = path.join(runtime, 'node_modules', '.omaax-document-lock');

function npmCli() {
  const candidates = [process.env.npm_execpath,
    path.join(path.dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js'),
    ...(process.env.PATH ?? '').split(path.delimiter).map(dir => path.join(dir, 'node_modules', 'npm', 'bin', 'npm-cli.js'))];
  const found = candidates.find(candidate => candidate && existsSync(candidate));
  if (!found) throw new Error('npm CLI is required to stage the document runtime. Run this script through npm or use a Node installation with npm.');
  return found;
}

async function check() {
  const root = path.join(runtime, 'node_modules', '@deepseek-ai', 'libreoffice-kit');
  const installed = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'));
  if (installed.version !== version) throw new Error(`Expected LibreOffice Kit ${version}, found ${installed.version}.`);
  const { discoverRuntime } = await import(pathToFileURL(path.join(root, 'lib', 'index.js')).href);
  const info = await discoverRuntime();
  const engineName = process.platform === 'linux' ? 'wasm' : `${process.platform}-${process.arch}`;
  const engine = path.join(runtime, 'node_modules', '@deepseek-ai', `libreoffice-kit-${engineName}`);
  for (const file of ['prebuilds.json', 'sources', 'licenses']) {
    if (!existsSync(path.join(engine, file))) throw new Error(`Document engine is incomplete: missing ${file}.`);
  }
  for (const forbidden of ['dsh', 'agent', 'skill-office']) {
    if (existsSync(path.join(runtime, 'node_modules', '@deepseek-ai', forbidden))) throw new Error(`Unexpected agent package in the isolated document runtime: ${forbidden}`);
  }
  return info;
}

let info;
if (process.argv.includes('--check')) info = await check();
else if (!process.argv.includes('--force') && existsSync(marker) && readFileSync(marker, 'utf8') === fingerprint) {
  try { info = await check(); } catch { /* Repair only the isolated document dependency tree below. */ }
}
if (!info) {
  const env = { ...process.env };
  // Downloads are direct from the official registry, never routed through a user proxy.
  for (const name of Object.keys(env)) if (/^(https?_proxy|all_proxy|npm_config_(https?_proxy|proxy))$/i.test(name)) delete env[name];
  env.npm_config_proxy = '';
  env.npm_config_https_proxy = '';
  const result = spawnSync(process.execPath, [npmCli(), 'ci', '--omit=dev', '--include=optional', '--ignore-scripts', '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org', '--fetch-retries=2', '--fetch-timeout=120000'], { cwd: runtime, env, stdio: 'inherit', windowsHide: true });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Document dependency installation failed (${result.status}).`);
  info = await check();
  writeFileSync(marker, fingerprint);
}
const crt = stageDocumentCrt({ runtime, checkOnly: process.argv.includes('--check') });
console.log(`Document resources verified: LibreOffice Kit ${info.version}, ${info.backend}, ${process.platform}-${process.arch}.${crt.required ? ` App-local Microsoft CRT ${crt.version}: ${crt.files} verified files.` : ''} The engine is checked on export.`);
