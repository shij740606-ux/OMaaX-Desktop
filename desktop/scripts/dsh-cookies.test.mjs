import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { EventEmitter } from 'node:events';
import net from 'node:net';
import vm from 'node:vm';
import { patchCookieCleanupSource, cookieCleanupSource } from './patch-dsh-cookies.mjs';

const source = await readFile(new URL('../dsh-runtime/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-connection/lib/index.js', import.meta.url), 'utf8');
const patched = patchCookieCleanupSource(source);
const secret = Buffer.alloc(32, 7);

function fixture(connect = net.connect) {
  const start = patched.indexOf('const AUTH_RECORD_KEY =');
  const end = patched.indexOf('//#endregion', start);
  assert.ok(start > 0 && end > start);
  const context = { Buffer, Headers, URL, createHash, createHmac, randomBytes, timingSafeEqual,
    credentialKey: () => 'test-only-auth-record', omaaxCookiePortConnect: connect };
  vm.runInNewContext(patched.slice(start, end) + '\nglobalThis.authTest={BrowserAuth,cookieName,encodeCookie,omaaxExpiredLocalCookies};', context);
  return context.authTest;
}

function cookie(api, authority, name = api.cookieName(authority)) {
  const issuedAt = Date.now() - 1000;
  return `${name}=${api.encodeCookie({ version: 1, authority, issuedAt, expiresAt: issuedAt + 86400000 }, secret)}`;
}
function threshold(entries, total = 32) {
  return [...entries, ...Array.from({ length: Math.max(0, total - entries.length) }, (_, i) => `dsh-auth-unrelated-${i}=malformed`)].join('; ');
}
async function listening(t) {
  const server = net.createServer(socket => socket.on('error', () => {}));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return server.address().port;
}
async function closedPort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
function fakeProbe(calls, outcome = 'ECONNREFUSED') {
  return options => {
    calls.push(options);
    const socket = new EventEmitter();
    socket.destroy = () => {};
    socket.setTimeout = (milliseconds, callback) => {
      assert.equal(milliseconds, 100);
      if (outcome === 'timeout') queueMicrotask(callback);
      return socket;
    };
    if (outcome !== 'timeout') queueMicrotask(() => outcome === 'connect' ? socket.emit('connect') : socket.emit('error', { code: outcome }));
    return socket;
  };
}
function response() {
  let done;
  const completed = new Promise(resolve => { done = resolve; });
  return { completed, writableEnded: false, destroyed: false,
    writeHead(status, headers) { this.status = status; this.headers = headers; },
    end(body) { this.body = body; this.writableEnded = true; done(); } };
}

test('cookie patch is idempotent and rejects modified or partial anchors', () => {
  assert.equal(patchCookieCleanupSource(patched), patched);
  assert.ok(patched.includes(cookieCleanupSource));
  assert.throws(() => patchCookieCleanupSource(patched.replace('cookies.size < 32', 'cookies.size < 31')), /generated region changed/);
  assert.throws(() => patchCookieCleanupSource(source.replace('tokenMatches(tokens.join(""), this.launchToken)', 'true')), /authentication contract changed/);
  assert.throws(() => patchCookieCleanupSource(source.replace('var BrowserAuth = class BrowserAuth {', 'var BrowserAuth = class {')), /anchor changed/);
  assert.throws(() => patchCookieCleanupSource(patched.replace('/*OMAAX_STALE_LOCAL_COOKIES_V1*/', '')), /Partial/);
});

test('fewer than 32 local-auth cookie names do not open any socket', async () => {
  const calls = [], api = fixture(fakeProbe(calls));
  const result = await api.omaaxExpiredLocalCookies(threshold([cookie(api, '127.0.0.1:30001')], 31), api.cookieName('127.0.0.1:30000'));
  assert.equal(result.length, 0);
  assert.equal(calls.length, 0);
});

test('real loopback probes expire only a refused old port and preserve current/live cookies', { timeout: 10000 }, async t => {
  const live = await listening(t), dead = await closedPort(), current = await closedPort();
  const calls = [], api = fixture(options => { calls.push(options.port); return net.connect(options); });
  const currentName = api.cookieName(`127.0.0.1:${current}`);
  const result = await api.omaaxExpiredLocalCookies(threshold([
    cookie(api, `127.0.0.1:${current}`), cookie(api, `127.0.0.1:${live}`), cookie(api, `127.0.0.1:${dead}`), 'user-preference=kept',
  ], 33), currentName);
  assert.equal(result.length, 1);
  assert.ok(result[0].startsWith(api.cookieName(`127.0.0.1:${dead}`) + '='));
  assert.match(result[0], /Max-Age=0; Path=\/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Strict$/);
  assert.deepEqual(calls.sort((a, b) => a - b), [live, dead].sort((a, b) => a - b));
  assert.ok(!result.some(value => value.startsWith(currentName + '=')));
});

test('malformed, foreign, invalid-port and mismatched-name cookies are neither probed nor removed', async () => {
  const calls = [], api = fixture(fakeProbe(calls));
  const entries = [
    cookie(api, '192.0.2.1:30001'), cookie(api, 'localhost:30001'), cookie(api, '[::1]:30001'),
    cookie(api, '127.0.0.1:0'), cookie(api, '127.0.0.1:65536'), cookie(api, '127.0.0.1:03001'),
    cookie(api, '127.0.0.1:30001', api.cookieName('127.0.0.1:30002')),
    `${api.cookieName('127.0.0.1:30003')}=v1.not+base64.invalid`,
    `${api.cookieName('127.0.0.1:30004')}=v2.${Buffer.from(JSON.stringify({version:1,authority:'127.0.0.1:30004'})).toString('base64url')}.${Buffer.alloc(32).toString('base64url')}`,
    'other-login-cookie=not-ours',
  ];
  assert.equal((await api.omaaxExpiredLocalCookies(threshold(entries), api.cookieName('127.0.0.1:30000'))).length, 0);
  assert.equal(calls.length, 0);
});

test('timeouts, live connections, other errors and probe exceptions always retain cookies', async () => {
  for (const outcome of ['timeout', 'connect', 'EACCES', 'ECONNRESET', 'ENETUNREACH']) {
    const calls = [], api = fixture(fakeProbe(calls, outcome));
    assert.equal((await api.omaaxExpiredLocalCookies(threshold([cookie(api, '127.0.0.1:30001')]), api.cookieName('127.0.0.1:30000'))).length, 0, outcome);
    assert.equal(calls.length, 1);
  }
  const api = fixture(() => { throw Error('Probe unavailable'); });
  assert.equal((await api.omaaxExpiredLocalCookies(threshold([cookie(api, '127.0.0.1:30001')]), api.cookieName('127.0.0.1:30000'))).length, 0);
});

test('each exchange probes at most 180 validated cookies and response cleanup stays bounded', async () => {
  const calls = [], api = fixture(fakeProbe(calls));
  const entries = Array.from({ length: 220 }, (_, i) => cookie(api, `127.0.0.1:${20000 + i}`));
  const result = await api.omaaxExpiredLocalCookies(entries.join('; '), api.cookieName('127.0.0.1:30000'));
  assert.equal(calls.length, 180);
  assert.equal(result.length, 180);
  assert.ok(calls.every(options => options.host === '127.0.0.1'));
  assert.ok(Buffer.byteLength(result.join('\r\n')) < 40000);
});

test('valid launch-token exchange stays synchronous false and responds after safe cleanup', async () => {
  const calls = [], api = fixture(fakeProbe(calls)), auth = new api.BrowserAuth({}, secret, 30);
  const authority = '127.0.0.1:30000', expiredName = api.cookieName('127.0.0.1:30001');
  const req = { method: 'GET', url: '/?token=' + encodeURIComponent(auth.launchToken), headers: { host: authority,
    cookie: threshold([cookie(api, authority), cookie(api, '127.0.0.1:30001')]) } };
  const res = response();
  assert.equal(auth.authorizeIndex(req, res), false);
  assert.equal(res.writableEnded, false);
  await res.completed;
  assert.equal(res.status, 303);
  assert.equal(res.headers.location, '/');
  assert.equal(res.headers['cache-control'], 'no-store');
  assert.equal(res.headers['set-cookie'].length, 2);
  assert.ok(res.headers['set-cookie'][0].startsWith(expiredName + '=; Max-Age=0;'));
  const current = res.headers['set-cookie'].at(-1);
  assert.ok(current.startsWith(api.cookieName(authority) + '=v1.'));
  assert.match(current, /HttpOnly; SameSite=Strict$/);
  assert.equal(auth.isAuthenticated({ headers: { host: authority, cookie: current.split(';')[0] } }), true);
});

test('unauthorized index requests still return 401 without probing or expiring cookies', async () => {
  const calls = [], api = fixture(fakeProbe(calls)), auth = new api.BrowserAuth({}, secret, 30);
  const raw = threshold([cookie(api, '127.0.0.1:30001')]);
  for (const [method, url] of [['GET', '/?token=invalid'], ['POST', '/?token=' + auth.launchToken], ['GET', '/'], ['GET', '/?token=' + auth.launchToken + '&token=extra']]) {
    const res = response();
    assert.equal(auth.authorizeIndex({ method, url, headers: { host: '127.0.0.1:30000', cookie: raw } }, res), false);
    await res.completed;
    assert.equal(res.status, 401);
    assert.equal(res.headers['set-cookie'], undefined);
  }
  assert.equal(calls.length, 0);
});

test('valid current cookie and invalid launch query keep the existing authentication semantics', async () => {
  const calls = [], api = fixture(fakeProbe(calls)), auth = new api.BrowserAuth({}, secret, 30);
  const headers = { host: '127.0.0.1:30000', cookie: threshold([cookie(api, '127.0.0.1:30000')]) };
  assert.equal(auth.authorizeIndex({ method: 'GET', url: '/', headers }, response()), true);
  const res = response();
  assert.equal(auth.authorizeIndex({ method: 'GET', url: '/?token=invalid', headers }, res), false);
  await res.completed;
  assert.equal(res.status, 303);
  assert.equal(res.headers['set-cookie'], undefined);
  assert.equal(calls.length, 0);
});
