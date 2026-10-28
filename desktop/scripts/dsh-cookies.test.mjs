import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdtemp } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { EventEmitter } from 'node:events';
import net from 'node:net';
import http from 'node:http';
import vm from 'node:vm';
import { patchCookieCleanupSource, cookieCleanupSource } from './patch-dsh-cookies.mjs';

const runtimePaths = ['../dsh-runtime/node_modules/@deepseek-ai/dsh-client-connection/lib/index.js','../dsh-runtime/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-connection/lib/index.js'].map(file=>new URL(file,import.meta.url));
const source = await readFile(runtimePaths.find(existsSync), 'utf8');
const patched = patchCookieCleanupSource(source);
const protocolScoped = source.includes('authorizeIndex(req, res, secure = false) {');
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

function cookie(api, authority, name = api.cookieName(authority), version = 1) {
  const issuedAt = Date.now() - 1000;
  return `${name}=${api.encodeCookie({ version, authority, ...(version === 2 ? {id:'test-api-operator'} : {}), issuedAt, expiresAt: issuedAt + 86400000 }, secret)}`;
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
  assert.equal(res.headers.location, protocolScoped ? './' : '/');
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

test('protocol-scoped contracts cannot be dropped to make the cookie patch apply', {skip: !protocolScoped}, () => {
  for (const contract of ['return secure ? url.origin : url.host;',
    'return secure ? `${attributes}; Secure` : attributes;',
    'return this.authenticate(request, secure)?.version === COOKIE_PAYLOAD_VERSION;',
    'return payload?.version === 2 ? payload.id : void 0;']) {
    assert.throws(()=>patchCookieCleanupSource(source.replace(contract, 'return true;')), /authentication contract changed/);
  }
});

test('TLS exchange keeps Secure cookies, relative redirects and protocol binding without cleanup', {skip: !protocolScoped}, async () => {
  const calls=[],api=fixture(fakeProbe(calls)),auth=new api.BrowserAuth({},secret,30);
  const host='127.0.0.1:30443',authority='https://'+host;
  const res=response();
  assert.equal(auth.authorizeIndex({method:'GET',url:'/?token='+auth.launchToken,
    headers:{host,cookie:threshold([cookie(api,'127.0.0.1:30001')])}},res,true),false);
  await res.completed;
  assert.equal(res.status,303);assert.equal(res.headers.location,'./');
  assert.equal(res.headers['set-cookie'].length,1);
  const current=res.headers['set-cookie'][0];
  assert.ok(current.startsWith(api.cookieName(authority)+'=v1.'));
  assert.match(current,/; HttpOnly; SameSite=Strict; Secure$/);
  const headers={host,cookie:current.split(';')[0]};
  assert.equal(auth.isAuthenticated({headers},true),true);
  assert.equal(auth.isAuthenticated({headers},false),false);
  assert.equal(auth.isAuthenticated({headers:{host,cookie:cookie(api,host)}},true),false);
  assert.equal(calls.length,0);
});

test('v2 API operators and TLS audiences are never cleanup candidates', {skip: !protocolScoped}, async () => {
  const calls=[],api=fixture(fakeProbe(calls));
  const apiAuthority='127.0.0.1:30001',tlsAuthority='https://127.0.0.1:30443';
  const result=await api.omaaxExpiredLocalCookies(threshold([
    cookie(api,apiAuthority,api.cookieName(apiAuthority),2),cookie(api,tlsAuthority),
  ]),api.cookieName('127.0.0.1:30000'));
  assert.equal(result.length,0);assert.equal(calls.length,0);
});

test('connect preserves independent API operators, interaction validation and browser separation', {skip: !protocolScoped}, async () => {
  const calls=[],api=fixture(fakeProbe(calls)),auth=new api.BrowserAuth({},secret,30);
  for(const secure of [false,true]){
    const host='127.0.0.1:30000',published=[],res=response();
    auth.authorizeConnect({method:'GET',url:'/connect?token='+auth.launchToken+'&interactions=answer',
      headers:{host,cookie:threshold([cookie(api,'127.0.0.1:30001')])}},res,secure,interaction=>{published.push(interaction);return 'operator-1';});
    await res.completed;
    assert.equal(res.status,302);assert.equal(res.headers.location,'./api/remote.mux');
    assert.deepEqual(published,['answer']);
    const value=res.headers['set-cookie'];assert.equal(typeof value,'string');
    assert.equal(value.endsWith('; Secure'),secure);assert.ok(value.includes('=v2.'));
    const headers={host,cookie:value.split(';')[0]};
    assert.equal(auth.authenticateApi({headers},secure),'operator-1');
    assert.equal(auth.isAuthenticated({headers},secure),false);
    assert.equal(auth.authenticateApi({headers:{host,cookie:cookie(api,secure?'https://'+host:host)}},secure),undefined);
  }
  for(const [url,status] of [['/connect?token=invalid',401],
    ['/connect?token='+auth.launchToken+'&interactions=answer&interactions=none',400],
    ['/connect?token='+auth.launchToken+'&interactions=invalid',400]]){
    const res=response();
    auth.authorizeConnect({method:'GET',url,headers:{host:'127.0.0.1:30000'}},res,false,()=>{throw Error('Invalid requests must not publish an operator');});
    await res.completed;assert.equal(res.status,status);assert.equal(res.headers['set-cookie'],undefined);
  }
  assert.equal(calls.length,0);
});

test('actual browser authentication code works across a real HTTP exchange after cleanup', {timeout:10000}, async t => {
  const api=fixture(),auth=new api.BrowserAuth({},secret,30),dead=await closedPort();
  const server=http.createServer((req,res)=>{if(auth.authorizeIndex(req,res)){res.writeHead(200);res.end('authenticated');}});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const port=server.address().port,authority=`127.0.0.1:${port}`;
  const request=(url,cookies)=>new Promise((resolve,reject)=>{
    const req=http.get({host:'127.0.0.1',port,path:url,agent:false,headers:{cookie:cookies,connection:'close'}},res=>{
      let body='';res.setEncoding('utf8');res.on('data',chunk=>body+=chunk);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body}));res.on('error',reject);
    });req.setTimeout(4000,()=>req.destroy(Error('Fixture HTTP timeout')));req.on('error',reject);
  });
  const exchanged=await request('/?token='+auth.launchToken,threshold([cookie(api,`127.0.0.1:${dead}`)]));
  assert.equal(exchanged.status,303);assert.equal(exchanged.headers.location,protocolScoped?'./':'/');
  const cookies=exchanged.headers['set-cookie'];
  assert.ok(cookies.some(value=>value.startsWith(api.cookieName(`127.0.0.1:${dead}`)+'=; Max-Age=0;')));
  const current=cookies.find(value=>value.startsWith(api.cookieName(authority)+'=v1.'));
  assert.ok(current);
  const loaded=await request('/',current.split(';')[0]);
  assert.equal(loaded.status,200);assert.equal(loaded.body,'authenticated');
  assert.equal((await request('/','')).status,401);
});

test('the installed DSH host accepts cleanup, serves its UI and preserves RPC authentication', {timeout:60000}, async t => {
  const home=await mkdtemp(path.join(os.tmpdir(),'omaax-auth-host-'));
  const cli=fileURLToPath(new URL('../dsh-runtime/node_modules/@deepseek-ai/dsh/lib/bin.js',import.meta.url));
  const env={...process.env,DSH_HOME:home,DSH_TELEMETRY_DISABLED:'1'};
  for(const key of Object.keys(env)){
    if (/^(OMAAX_|BIODSH_|DEEPSEEK_|OPENAI_|ANTHROPIC_|GOOGLE_|NODE_OPTIONS$|HTTPS?_PROXY$|ALL_PROXY$)/i.test(key))delete env[key];
  }
  const child=spawn(process.execPath,['--expose-internals','--max-http-header-size=65536',cli,'web','--port','0','--no-open'],{cwd:home,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  t.after(async()=>{
    if(child.exitCode!==null||child.signalCode!==null)return;
    await new Promise(resolve=>{child.once('exit',resolve);child.kill();});
  });
  const tokenUrl=await new Promise((resolve,reject)=>{
    let output='';
    const timer=setTimeout(()=>reject(Error('Isolated DSH host startup timed out')),40000);
    const scan=chunk=>{
      output=(output+chunk.toString()).slice(-80000);
      const match=output.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/);
      if(match){clearTimeout(timer);resolve(match[0]);}
    };
    child.stdout.on('data',scan);child.stderr.on('data',scan);
    child.once('error',error=>{clearTimeout(timer);reject(error);});
    child.once('exit',code=>{clearTimeout(timer);reject(Error('Isolated DSH host exited with code '+code));});
  });
  const origin=new URL(tokenUrl).origin,api=fixture(),dead=await closedPort(),oldAuthority=`127.0.0.1:${dead}`;
  const exchanged=await fetch(tokenUrl,{redirect:'manual',headers:{cookie:threshold([cookie(api,oldAuthority)])},signal:AbortSignal.timeout(5000)});
  assert.equal(exchanged.status,303);
  assert.equal(exchanged.headers.get('location'),protocolScoped?'./':'/');
  const values=exchanged.headers.getSetCookie();
  assert.ok(values.some(value=>value.startsWith(api.cookieName(oldAuthority)+'=; Max-Age=0;')));
  const current=values.find(value=>value.startsWith(api.cookieName(new URL(origin).host)+'=v1.'));
  assert.ok(current);
  const headers={cookie:current.split(';')[0]},options={headers,signal:AbortSignal.timeout(5000)};
  const index=await fetch(origin+'/',options);assert.equal(index.status,200);assert.match(await index.text(),/<title>OMaaX<\/title>/);
  const rejected=await fetch(origin+'/',{signal:AbortSignal.timeout(5000)});assert.equal(rejected.status,401);
  assert.match(await rejected.text(),/本地连接认证已失效/);
  const listed=await fetch(origin+'/api/session/list',{method:'POST',headers:{...headers,'content-type':'application/json'},
    body:JSON.stringify({type:'client-request',rpcId:'auth-only-regression',method:'session/list',payload:{args:{_request:{}}}}),signal:AbortSignal.timeout(5000)});
  assert.equal(listed.status,200);assert.equal((await listed.json()).result.ok,true);
});
