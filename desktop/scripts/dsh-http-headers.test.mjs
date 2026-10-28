import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const platform = process.platform === 'win32' ? 'win32-x64' : process.platform === 'darwin' ? `darwin-${process.arch}` : 'linux-x64';
const node = path.join(root, 'resources/node', platform, process.platform === 'win32' ? 'node.exe' : 'bin/node');
const source = await readFile(path.join(root, 'src-tauri/src/dsh.rs'), 'utf8');
const headerArgument = source.match(/cmd\.arg\("--expose-internals"\)\.arg\("(--max-http-header-size=\d+)"\)\.arg\(&bin\)/)?.[1];

// Signed cookies from different local ports share one host. These values are synthetic.
const cookies = Array.from({ length: 66 }, (_, index) => `dsh-auth-${50000 + index}=${'x'.repeat(210)}`).join('; ');
const batchPrefix = '/plugins/??@fixture/plugin/client.js,';
const batchSuffix = '&rev=000000000000';
const batch = batchPrefix + 'p'.repeat(1701 - batchPrefix.length - batchSuffix.length) + batchSuffix;

async function server(args, t) {
  const env = { ...process.env };
  delete env.NODE_OPTIONS;
  const child = spawn(node, [...args, '--input-type=module', '-e', `
    import http from 'node:http';
    const server = http.createServer((request, response) => {
      response.writeHead(200, {'content-type':'application/json'});
      response.end(JSON.stringify({url:request.url,cookie:request.headers.cookie}));
    });
    server.listen(0, '127.0.0.1', () => process.stdout.write(String(server.address().port)+'\\n'));
  `], { windowsHide: true, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  t.after(async () => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    await new Promise(resolve => { child.once('exit', resolve); child.kill(); });
  });
  return await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(Error(`Local HTTP fixture did not start: ${stderr}`)); }, 10000);
    let stdout = '';
    const finish = (error, value) => { clearTimeout(timer); error ? reject(error) : resolve(value); };
    child.once('error', error => finish(error));
    child.once('exit', code => finish(Error(`Local HTTP fixture exited (${code}): ${stderr}`)));
    child.stdout.on('data', chunk => { stdout += chunk; if (/^\d+\n/.test(stdout)) finish(null, Number(stdout.trim())); });
  });
}

function request(port, url, cookie) {
  return new Promise((resolve, reject) => {
    const request = http.get({ hostname: '127.0.0.1', port, path: url, agent: false, headers: { Cookie: cookie, Connection: 'close' } }, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => resolve({ status: response.statusCode, body }));
      response.on('error', reject);
    });
    request.setTimeout(10000, () => request.destroy(Error('Local HTTP fixture request timed out')));
    request.once('error', reject);
  });
}

test('default Node limit reproduces short index success and long plugin batch HTTP 431', { timeout: 20000 }, async t => {
  assert.equal(Buffer.byteLength(batch), 1701);
  assert.equal(cookies.split('; ').length, 66);
  const port = await server([], t);
  const index = await request(port, '/', cookies);
  assert.equal(index.status, 200);
  assert.equal(JSON.parse(index.body).cookie, cookies);
  assert.equal((await request(port, batch, cookies)).status, 431);
});

test('the actual desktop Node argument accepts the same request without dropping cookies', { timeout: 20000 }, async t => {
  assert.equal(headerArgument, '--max-http-header-size=65536');
  const port = await server([headerArgument], t);
  for (const url of ['/', batch]) {
    const response = await request(port, url, cookies);
    assert.equal(response.status, 200);
    assert.deepEqual(JSON.parse(response.body), { url, cookie: cookies });
  }
});

test('the desktop still rejects a request exceeding its finite 64 KiB header limit', { timeout: 20000 }, async t => {
  assert.equal(headerArgument, '--max-http-header-size=65536');
  const port = await server([headerArgument], t);
  assert.equal((await request(port, batch, 'synthetic=' + 'x'.repeat(65536))).status, 431);
});
