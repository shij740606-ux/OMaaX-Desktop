import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {patchAuthErrorSource, authErrorText} from './patch-dsh-auth-error.mjs';

const original = readFileSync(new URL('../dsh-runtime/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-connection/lib/index.js', import.meta.url), 'utf8');
const patched = patchAuthErrorSource(original);
const method = source => source.match(/\twriteUnauthorized\(req, res\) \{[\s\S]*?\n\t\}/)?.[0];
const run = (source, httpMethod) => {
  assert.ok(method(source), 'Actual authentication responder must be found');
  const handler = vm.runInNewContext(`({${method(source)}}).writeUnauthorized`);
  const result = {headers:{}};
  const res = {writeHead:(statusCode,headers)=>{res.statusCode=statusCode;result.headers={...headers};}, setHeader:(key,value)=>result.headers[key]=value, end:body=>result.body=body};
  handler.call({}, {method:httpMethod}, res);
  return {...result, statusCode:res.statusCode};
};

test('authentication rejection remains 401 and replaces only its body with actionable Chinese', () => {
  const before = run(original, 'GET'), after = run(patched, 'GET');
  assert.equal(after.statusCode, 401);
  assert.deepEqual(after.headers, before.headers);
  assert.equal(after.body, authErrorText);
  assert.doesNotMatch(after.body, /deepseek|harness|\bdsh\b/i);
  assert.equal(patched.replace(method(patched), ''), original.replace(method(original), ''));
});

test('HEAD still rejects authentication without a response body', () => {
  const before = run(original, 'HEAD'), after = run(patched, 'HEAD');
  assert.deepEqual(after, before);
  assert.equal(after.statusCode, 401);
  assert.equal(after.body, undefined);
});

test('patch is idempotent and rejects missing, duplicate, or edited anchors', () => {
  assert.equal(patchAuthErrorSource(patched), patched);
  assert.throws(()=>patchAuthErrorSource('unexpected upstream layout'), /anchor changed/);
  assert.throws(()=>patchAuthErrorSource(original + original), /(?:anchor|region) changed/);
  assert.throws(()=>patchAuthErrorSource(patched.replace(authErrorText.trim(), 'modified wording')), /region changed/);
});
