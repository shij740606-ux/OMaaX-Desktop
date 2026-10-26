import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {patchApprovalSource} from './patch-dsh-approval.mjs';

const file = new URL('../dsh-runtime/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-ui-chat/lib/client.js', import.meta.url);
const source = patchApprovalSource(readFileSync(file, 'utf8'));
const region = source.slice(source.indexOf('\t\tfunction commandOf('), source.indexOf('\t\t//#endregion', source.indexOf('\t\tfunction commandOf(')));
const {ApprovalCommand} = vm.runInNewContext(region + '\n({ApprovalCommand})');
const call = (id, command, subCalls = []) => ({callId:id, argsRaw:JSON.stringify({command}), subCalls});
const show = (roots, id) => ApprovalCommand({callId:id, useChat:selector => selector({nodes:new Map(roots.map((root,i) => [i,{kind:'tool-call',data:{root}}]))}), t:key => key});

test('approval shows the exact direct or nested command, including restored result wrappers', () => {
  const wanted = call('root:ptc:2', 'Get-Content C:/data/input.txt');
  const roots = [call('root','unrelated parent',[call('root:ptc:1','unrelated sibling'),wanted])];
  assert.equal(show(roots, wanted.callId),'Get-Content C:/data/input.txt');
  assert.equal(show([wanted], wanted.callId),'Get-Content C:/data/input.txt');
  assert.equal(show([{kind:'tool-result',callId:wanted.callId,call:wanted,subCalls:[]}], wanted.callId),'Get-Content C:/data/input.txt');
  assert.equal(show([{kind:'tool-result',callId:wanted.callId,call:null,subCalls:[]}], wanted.callId),'approval.detailUnavailable');
  assert.equal(show([call('other','DO NOT USE THIS')], wanted.callId),'approval.detailUnavailable');
});

test('history arrival replaces unavailable details without borrowing another operation', () => {
  const wanted = call('approved','Write-Output hello');
  assert.equal(show([],wanted.callId),'approval.detailUnavailable');
  assert.equal(show([wanted],wanted.callId),'Write-Output hello');
  assert.equal(show([{...wanted,argsRaw:'{"path":"C:/data"}'}],wanted.callId),null);
  assert.equal(show([{...wanted,argsRaw:'malformed'}],wanted.callId),null);
  const cyclic = call('cycle','wrong'); cyclic.subCalls.push(cyclic);
  assert.equal(show([cyclic],wanted.callId),'approval.detailUnavailable');
});

test('patch is idempotent, localized, build-integrated and fails on upstream drift', () => {
  assert.equal(patchApprovalSource(source),source);
  assert.match(source,/name: "conversation.approval.detail", locale: NS/);
  assert.match(source,/暂未读取到这次操作的详细参数/);
  assert.match(source,/Details for this operation are not available yet/);
  assert.throws(() => patchApprovalSource('function unknownApproval() {}'),/implementation changed/);
  const staging = readFileSync(new URL('./stage-dsh.mjs',import.meta.url),'utf8');
  assert.match(staging,/patchApproval\(dir\)/);
});

test('branding only hides the hero headline and empty approval reasons use the tool fallback', () => {
  const native = readFileSync(new URL('../src-tauri/src/lib.rs',import.meta.url),'utf8');
  assert.match(native,/\[class\*="_headline"\]:has\(> \[class\*="_fishHitbox"\]\)/);
  assert.doesNotMatch(native,/\[class\*="_headline"\]\s*[,\{]/);
  const panel = readFileSync(new URL('../dsh-runtime/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-ui-approval/lib/client.js',import.meta.url),'utf8');
  assert.match(panel,/pending.reason\?\.trim\(\) \|\| t\("escalation"/);
  const expression = panel.match(/\/\*OMAAX_APPROVAL_REASON_V1\*\/ ([^\n]+)/)[1];
  const fallback = reason => vm.runInNewContext(expression, {pending:{reason,toolName:'my_tool'},t:(_,values)=>`Tool ${values.toolName} requests privileged execution`});
  assert.equal(fallback('  '),'Tool my_tool requests privileged execution');
  assert.equal(fallback('Confirm writing results'),'Confirm writing results');
});
