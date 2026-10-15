import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const env = { ...process.env, CI: 'true', BIODSH_TARGETS: `${process.platform}-${process.arch}`, NPM_REGISTRY: process.env.NPM_REGISTRY || 'https://registry.npmjs.org/' };
for (const script of ['fetch-node.mjs', 'fetch-uv.mjs', 'stage-dsh.mjs', 'sync-skills.mjs', 'stage-ssh.mjs']) {
  execFileSync(process.execPath, [`scripts/${script}`], { cwd: root, env, stdio: 'inherit' });
}
// Embeddings are optional; install their implementation, but no model weights.
execFileSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund', '--registry=https://registry.npmjs.org/'], { cwd: `${root}/resources/embed`, env, stdio: 'inherit', shell: process.platform === 'win32' });
if (process.platform === 'darwin') execFileSync(process.execPath, ['scripts/prep-tauri-platform.mjs'], { cwd: root, env, stdio: 'inherit' });
console.log('OMaaX 开发依赖准备完成；未安装社区工具、分析软件或参考数据库。');
