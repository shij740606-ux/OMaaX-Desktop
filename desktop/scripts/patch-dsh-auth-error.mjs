// Change only the public authentication failure body; leave the authentication boundary intact.
import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const authErrorText = '本地连接认证已失效，请点击左上角“重启智能体”后重试。\n';
const original = 'res.end(req.method === "HEAD" ? void 0 : "dsh web authentication required; reopen the URL printed by dsh web.\\n");';
const replacement = `/*OMAAX_AUTH_ERROR_ZH_V1*/ res.end(req.method === "HEAD" ? void 0 : ${JSON.stringify(authErrorText)});`;

export function patchAuthErrorSource(source) {
  const marker = '/*OMAAX_AUTH_ERROR_ZH_V1*/';
  if (source.includes(marker)) {
    if (source.split(replacement).length !== 2 || source.includes(original)) throw Error('Authentication error generated region changed');
    return source;
  }
  if (source.split(original).length !== 2 || !source.includes('writeUnauthorized(req, res) {')) throw Error('Authentication error patch anchor changed');
  return source.replace(original, replacement);
}

export function patchDshAuthError(runtimeDir = path.join(desktop, 'dsh-runtime')) {
  const files = ['node_modules/@deepseek-ai/dsh-client-connection/lib/index.js', 'node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-connection/lib/index.js']
    .map(file => path.join(runtimeDir, file)).filter(existsSync);
  if (!files.length) throw Error('Authentication error runtime missing');
  // Validate every layout before writing so version drift cannot leave a partially patched runtime.
  const patched = files.map(file => [file, patchAuthErrorSource(readFileSync(file, 'utf8'))]);
  for (const [file, source] of patched) writeFileSync(file, source);
  return files;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  console.log(`Local authentication message ready (${patchDshAuthError(process.argv[2]).length} files)`);
}
