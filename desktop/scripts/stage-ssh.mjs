import { existsSync, readFileSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const directory = fileURLToPath(new URL('../resources/ssh/', import.meta.url));
const workflowGuard=new URL('../dsh-runtime/node_modules/@biodsh/workflow-guard/',import.meta.url);
mkdirSync(workflowGuard,{recursive:true});
for(const file of ['index.js','package.json'])copyFileSync(new URL('../dsh-plugins/workflow-guard/'+file,import.meta.url),new URL(file,workflowGuard));
const stamp = new URL('../resources/ssh/.stage-lock', import.meta.url);
const fingerprint = createHash('sha256').update(readFileSync(new URL('../resources/ssh/package-lock.json', import.meta.url))).digest('hex');
if (!existsSync(new URL('../resources/ssh/node_modules/ssh2/package.json', import.meta.url)) || !existsSync(stamp) || readFileSync(stamp, 'utf8') !== fingerprint) {
  execSync('npm ci --omit=dev --ignore-scripts --registry=https://registry.npmjs.org', { cwd: directory, stdio: 'inherit' });
  writeFileSync(stamp, fingerprint);
}
