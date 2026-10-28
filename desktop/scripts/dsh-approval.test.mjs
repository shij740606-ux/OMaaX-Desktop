import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import {create, act} from 'react-test-renderer';
import {patchApprovalSource, patchApprovalPanelSource} from './patch-dsh-approval.mjs';

const base = ['../dsh-runtime/node_modules/@deepseek-ai/', '../dsh-runtime/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/']
  .map(p => new URL(p, import.meta.url)).find(p => existsSync(new URL('dsh-client-ui-chat/lib/client.js', p)));
assert.ok(base, 'installed DSH chat client exists');
const source = patchApprovalSource(readFileSync(new URL('dsh-client-ui-chat/lib/client.js', base), 'utf8'));
const panel = patchApprovalPanelSource(readFileSync(new URL('dsh-client-ui-approval/lib/client.js', base), 'utf8'));
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
  assert.match(panel,/(?:pending\.)?reason\?\.trim\(\) \|\| t\("escalation"/);
  const expression = panel.match(/\/\*OMAAX_APPROVAL_REASON_V1\*\/ ([^\n]+)/)[1];
  const fallback = reason => vm.runInNewContext(expression, {reason,pending:{reason,toolName:'my_tool'},t:(_,values)=>`Tool ${values.toolName} requests privileged execution`});
  assert.equal(fallback('  '),'Tool my_tool requests privileged execution');
  assert.equal(fallback('Confirm writing results'),'Confirm writing results');
});

test('both approval reason contracts patch exactly once and reject upstream drift', () => {
  for (const fixture of [
    'pending.reason ?? t("escalation", { toolName: pending.toolName })',
    'function ApprovalFlow({ pending, reason, detail, t }) { return reason ?? t("escalation", { toolName: pending.toolName }); }',
  ]) {
    const patched = patchApprovalPanelSource(fixture);
    assert.equal(patchApprovalPanelSource(patched), patched);
    assert.throws(() => patchApprovalPanelSource(fixture + fixture), /anchor changed/);
    assert.throws(() => patchApprovalPanelSource(patched.replace('?.trim()', '')), /reason changed/);
  }
  assert.throws(() => patchApprovalPanelSource('unknown implementation'), /anchor changed/);
  assert.match(panel, /reason: approval.displayReason === void 0 \? approval.reason : props.resolveReason\(approval.displayReason\)/);
});

// Execute the installed module and its PendingApproval state; only visual primitives
// are reduced to host elements so React can test interactions without a browser.
function installedPanel() {
  let exported;
  const primitives = {
    Button: ({variant, ...props}) => React.createElement('button', props),
    StateDot: ({state}) => React.createElement('span', {'data-state': state}),
  };
  const instrumented = panel.replace('return module.exports;', 'return { ...module.exports, ApprovalPanel, PendingApproval, zh };');
  vm.runInNewContext(instrumented, {Promise, window: {__ModuleLoader__: {load: module => {
    exported = module.factory(name => {
      if (name === 'react') return React;
      if (name === 'react/jsx-runtime') return jsxRuntime;
      if (name === '@deepseek-ai/dsh-client-ui-primitives') return primitives;
      throw new Error(`unexpected approval dependency: ${name}`);
    });
  }}}});
  return exported;
}
const {ApprovalPanel, PendingApproval, zh} = installedPanel();
const t = (key, values = {}) => (zh[key] ?? key).replace(/\{(\w+)\}/g, (_, name) => values[name] ?? '');
const pendingRequest = (request = {}) => new PendingApproval('isolated-session', {toolName: 'shell', callId: 'target', reason: '  ', ...request});
function mountApproval(pending, roots = []) {
  let snapshot = {nodes: new Map(roots.map((root, i) => [i, {kind: 'tool-call', data: {root}}]))};
  const listeners = new Set();
  const useChat = selector => selector(React.useSyncExternalStore(
    listener => { listeners.add(listener); return () => listeners.delete(listener); },
    () => snapshot,
  ));
  let renderer;
  act(() => { renderer = create(React.createElement(ApprovalPanel, {
    matched: pending,
    t,
    resolveReason: reason => reason.text,
    renderSlot: (name, props) => {
      assert.equal(name, 'conversation.approval.detail');
      return React.createElement(ApprovalCommand, {...props, useChat, t: key => key});
    },
  })); });
  return {
    renderer,
    text: () => JSON.stringify(renderer.toJSON()),
    buttons: () => renderer.root.findAllByType('button'),
    restore(roots) {
      act(() => {
        snapshot = {nodes: new Map(roots.map((root, i) => [i, {kind: 'tool-call', data: {root}}]))};
        for (const listener of listeners) listener();
      });
    },
    close: () => act(() => renderer.unmount()),
  };
}

test('installed approval UI restores exact command, Chinese reason and one-shot acceptance', async () => {
  const pending = pendingRequest({displayReason: {text: '允许写入当前项目的结果文件'}});
  const h = mountApproval(pending, [call('wrong', 'do not display')]);
  try {
    assert.match(h.text(), /允许写入当前项目的结果文件/);
    assert.match(h.text(), /approval.detailUnavailable/);
    assert.doesNotMatch(h.text(), /do not display/);
    h.restore([call('parent', 'parent command', [call('target', 'Write-Output exact-command')])]);
    assert.match(h.text(), /Write-Output exact-command/);
    assert.doesNotMatch(h.text(), /parent command|detailUnavailable/);
    assert.deepEqual(h.buttons().map(button => button.children.join('')), ['拒绝', '允许一次']);
    await act(async () => { h.buttons()[1].props.onClick(); });
    assert.equal(await pending.result, 'allowed-once');
    assert.equal(pending.answerable, false);
    assert.ok(h.buttons().every(button => button.props.disabled));
    await act(async () => { h.buttons()[0].props.onClick(); });
    assert.equal(await pending.result, 'allowed-once');
  } finally { h.close(); }
});

test('installed approval UI retries failed settlement and preserves refusal/abort boundaries', async () => {
  const pending = pendingRequest();
  const answer = pending.answer.bind(pending);
  let attempts = 0;
  pending.answer = outcome => ++attempts === 1 ? Promise.reject(new Error('transient transport failure')) : answer(outcome);
  const h = mountApproval(pending);
  try {
    assert.match(h.text(), /工具 shell 请求越权执行/);
    await act(async () => { h.buttons()[1].props.onClick(); });
    assert.ok(h.buttons().every(button => !button.props.disabled));
    assert.equal(pending.answerable, true);
    await act(async () => { h.buttons()[0].props.onClick(); });
    assert.equal(await pending.result, 'rejected');
    assert.equal(attempts, 2);
  } finally { h.close(); }
  const controller = new AbortController();
  const withdrawn = pendingRequest({signal: controller.signal});
  const rejected = assert.rejects(withdrawn.result, /scope ended/);
  controller.abort(new Error('scope ended'));
  await rejected;
  assert.equal(withdrawn.answerable, false);
  await assert.rejects(withdrawn.answer('allowed-once'), /already settled/);
});
