import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');

function harness({ rejectPlay = false, startup, storage = new Map(), storageUnavailable = false } = {}) {
  let now = 0, sequence = 0;
  const timers = new Map();
  globalThis.window = {
    __OMAAX_STARTUP__: startup,
    sessionStorage: {
      getItem: key => { if (storageUnavailable) throw Error('storage disabled'); return storage.get(key) ?? null; },
      setItem: (key, value) => { if (storageUnavailable) throw Error('storage disabled'); storage.set(key, value); },
    },
    setTimeout: (fn, delay) => { const id = ++sequence; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout: id => timers.delete(id),
  };
  const source = fs.readFileSync(new URL('../src/renderer/src/components/StartupIntro.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', code)(name => /\.(css|mp4|jpg)$/.test(name) ? name : require(name), module, module.exports);
  const player = new EventTarget();
  player.play = () => rejectPlay ? Promise.reject(Error('unsupported codec')) : Promise.resolve();
  player.pause = () => {};
  return {
    Intro: module.exports.default,
    player,
    nodeMock: element => element.type === 'video' ? player : {},
    tick: ms => { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); } },
    timers,
  };
}

test('login is mounted only after the video ends and account changes do not replay the intro', async () => {
  const h = harness(); let view, mounts = 0;
  function Login({ account }) { React.useEffect(() => { mounts++; }, []); return React.createElement('form', null, account); }
  await act(async () => { view = create(React.createElement(h.Intro, null, React.createElement(Login, { account: 'first' })), { createNodeMock: h.nodeMock }); });
  assert.equal(mounts, 0); assert.equal(view.root.findAllByType('form').length, 0);
  const video = view.root.findByType('video');
  assert.equal(video.props.autoPlay, true); assert.equal(video.props.muted, true); assert.equal(video.props.playsInline, true);
  await act(async () => { h.player.dispatchEvent(new Event('playing')); h.tick(2000); h.player.dispatchEvent(new Event('timeupdate')); h.tick(2000); });
  assert.equal(mounts, 0);
  await act(async () => h.player.dispatchEvent(new Event('ended')));
  assert.equal(mounts, 1); assert.equal(view.root.findAllByType('video').length, 0); assert.equal(h.timers.size, 0);
  await act(async () => view.update(React.createElement(h.Intro, null, React.createElement(Login, { account: 'second' }))));
  assert.equal(mounts, 1); assert.equal(view.root.findByType('form').children[0], 'second');
  await act(async () => view.unmount());
});

for (const failure of ['error', 'rejected playback', 'load timeout', 'playback stall', 'absolute timeout']) {
  test(`sign-in remains reachable after ${failure}`, async () => {
    const h = harness({ rejectPlay: failure === 'rejected playback' }); let view;
    await act(async () => { view = create(React.createElement(h.Intro, null, React.createElement('form')), { createNodeMock: h.nodeMock }); });
    await act(async () => {
      if (failure === 'error') h.player.dispatchEvent(new Event('error'));
      if (failure === 'load timeout') h.tick(8000);
      if (failure === 'playback stall') { h.player.dispatchEvent(new Event('playing')); h.tick(3000); }
      if (failure === 'absolute timeout') { for (let i = 0; i < 15; i++) { h.player.dispatchEvent(new Event('timeupdate')); h.tick(1000); } }
    });
    assert.equal(view.root.findAllByType('form').length, 1); assert.equal(h.timers.size, 0);
    await act(async () => view.unmount());
  });
}

test('closing during the intro clears every timer', async () => {
  const h = harness(); let view;
  await act(async () => { view = create(React.createElement(h.Intro, null, React.createElement('form')), { createNodeMock: h.nodeMock }); });
  assert.equal(h.timers.size, 2);
  await act(async () => view.unmount());
  assert.equal(h.timers.size, 0);
});

test('login and logout process restarts skip the intro even with a different empty account profile', async () => {
  for (const id of ['login-process', 'logout-process']) {
    const h = harness({ startup: { id, skipIntro: true } }); let view;
    await act(async () => { view = create(React.createElement(h.Intro, null, React.createElement('form')), { createNodeMock: h.nodeMock }); });
    assert.equal(view.root.findAllByType('video').length, 0);
    assert.equal(view.root.findAllByType('form').length, 1);
    assert.equal(h.timers.size, 0);
    await act(async () => view.unmount());
  }
});

test('completed cold launch survives a full document reload but a new cold launch plays again', async () => {
  const storage = new Map(); let view;
  for (const [id, shouldPlay] of [['first-process', true], ['first-process', false], ['reopened-process', true]]) {
    const h = harness({ startup: { id, skipIntro: false }, storage });
    await act(async () => { view = create(React.createElement(h.Intro, null, React.createElement('form')), { createNodeMock: h.nodeMock }); });
    assert.equal(view.root.findAllByType('video').length, shouldPlay ? 1 : 0);
    if (shouldPlay) await act(async () => h.player.dispatchEvent(new Event('ended')));
    assert.equal(view.root.findAllByType('form').length, 1);
    await act(async () => view.unmount());
  }
});

test('playback failure is remembered for this launch and unavailable storage does not replay on remount', async () => {
  for (const storageUnavailable of [false, true]) {
    const h = harness({ startup: { id: 'failed-video', skipIntro: false }, storageUnavailable }); let view;
    const mount = async () => { await act(async () => { view = create(React.createElement(h.Intro, null, React.createElement('form')), { createNodeMock: h.nodeMock }); }); };
    await mount();
    await act(async () => h.player.dispatchEvent(new Event('error')));
    await act(async () => view.unmount());
    await mount();
    assert.equal(view.root.findAllByType('video').length, 0);
    assert.equal(view.root.findAllByType('form').length, 1);
    await act(async () => view.unmount());
  }
});

test('native account restart wiring preserves authentication and skips only the presentation', () => {
  const source = fs.readFileSync(new URL('../src-tauri/src/lib.rs', import.meta.url), 'utf8');
  for (const command of ['hub_login', 'hub_logout']) {
    const start = source.indexOf(`async fn ${command}(`);
    const body = source.slice(start, source.indexOf('\n#[tauri::command]', start));
    assert.match(body, /startup::arm_account_restart\(\);\s*instance::release\(\);\s*app.restart\(\);/);
    assert.match(body, command === 'hub_login' ? /hub_auth::arm_login_restart\(\)/ : /spawn_blocking\(hub_auth::logout\)/);
  }
  assert.match(source, /pub fn run\(\)\s*\{\s*startup::initialize\(\);/);
  assert.match(source, /WebviewBuilder::new\("ui"[\s\S]*?initialization_script\(startup::initialization_script\(\)\)/);
});
