import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = fileURLToPath(new URL('../', import.meta.url));
const realRequire = createRequire(path.join(root, 'package.json'));
const React = realRequire('react');
const { act, create } = realRequire('react-test-renderer');
const text = node => typeof node === 'string' ? node : (node.children || []).map(text).join('');
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

function harness({ state = {}, api = {}, lang = 'zh' } = {}) {
  const modules = new Map(), events = new EventTarget(), data = new Map();
  globalThis.localStorage = { getItem: k => data.get(k) || null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) };
  globalThis.window = { biodsh: { migrateScan: async () => [], ratingsGet: async () => ({}), ...api }, addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events) };
  const app = { settings: { mode: 'online', mcpServers: [], workspace: 'test-workspace' }, credential: {}, info: { version: '0.4.16', paths: { root: 'test-root' } }, refresh: async () => {}, updateSettings: async () => {}, restartDsh: () => {}, env: { ready: false, step: 'idle', log: [] }, catalog: [], statuses: {}, dsh:{state:"stopped"}, ...state };
  const load = file => {
    if (modules.has(file)) return modules.get(file).exports;
    const module = { exports: {} }; modules.set(file, module);
    const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
    const require = name => {
      if (['react', 'react/jsx-runtime'].includes(name)) return realRequire(name);
      if (name === 'lucide-react') return new Proxy({}, { get: (_, k) => k === '__esModule' ? false : () => null });
      if (name.endsWith('/AuthGate')) return {useAuth:()=>({user:{role:'admin',authorizations:[]}}),hasUnit:()=>true};
      if (name.endsWith('/store')) return { useApp: () => app };
      if (name.endsWith('/i18n')) return { useT: () => ({ t: (value, vars) => Object.entries(vars || {}).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, String(v)), value), lang }) };
      if (name.endsWith('/icons')) return new Proxy({}, { get: () => () => null });
      if (/\/(InstallButton|Markdown|SudoInstall)$/.test(name)) return { __esModule: true, default: () => null };
      if (name.startsWith('.')) { const p = path.resolve(path.dirname(file), name); return load(['', '.ts', '.tsx'].map(e => p + e).find(p => fs.existsSync(p))); }
      throw Error('Unexpected dependency: ' + name);
    };
    new Function('require', 'module', 'exports', 'setInterval', 'clearInterval', code)(require, module, module.exports, () => 0, () => {});
    return module.exports;
  };
  return { load: relative => load(path.join(root, 'src/renderer/src', relative)), app };
}

test('help explains model, execution location, on-demand tools and shared resource limits', () => {
  const { HELP } = harness().load('components/HelpTip.tsx');
  assert.match(HELP.model[2], /无需填写访问密钥/);
  assert.match(HELP.mcp[2], /预设只是配置起点.*与 SSH 分析服务器是两种/);
  assert.match(HELP.env[2], /只在你点击安装时添加/);
  assert.match(HELP.tools[2], /不代表所有分析软件都已安装/);
  assert.match(HELP.quota[2], /20 核、50 GiB、24 小时/);
  assert.match(HELP.resources[1], /不是本次任务的配额/);
});

test('help is keyboard-accessible, closes on Escape, and does not activate a parent control', async () => {
  const Tip = harness().load('components/HelpTip.tsx').default;
  let view; await act(async () => { view = create(React.createElement(Tip, { topic: 'image' })); });
  assert.equal(view.root.findByType('summary').props['aria-label'], '查看说明：图像服务');
  assert.equal(view.root.findByProps({ role: 'note' }).findByType('strong').children[0], '图像服务');
  let stopped = 0, focused = 0;
  const node = { open: true, querySelector: () => ({ focus: () => focused++ }) };
  view.root.findByType('details').props.onKeyDown({ key: 'Escape', currentTarget: node, stopPropagation: () => stopped++ });
  view.root.findByType('details').props.onClick({ stopPropagation: () => stopped++ });
  assert.equal(node.open, false); assert.equal(focused, 1); assert.equal(stopped, 2);
  await act(async () => view.unmount());
});

test('English help does not expose untranslated feature tips', async () => {
  const { default: Tip, HELP } = harness({ lang: 'en' }).load('components/HelpTip.tsx');
  for (const topic of Object.keys(HELP)) {
    let view; await act(async () => { view = create(React.createElement(Tip, { topic })); });
    assert.doesNotMatch(text(view.root), /[\u4e00-\u9fff]/);
    await act(async () => view.unmount());
  }
});

test('account settings expose restored connectors without importing legacy data',async()=>{
  const calls=[];const View=harness({api:{migrateScan:async()=>{calls.push('scan');return [];}}}).load('views/SettingsView.tsx').default;
  let view;await act(async()=>{view=create(React.createElement(View));await settle();});
  assert.match(text(view.root),/图像服务/); assert.match(text(view.root),/外部工具连接（MCP）/);
  assert.doesNotMatch(text(view.root),/已有助手服务地址|中枢|旧版|新版/);
  assert.deepEqual(calls,[]);await act(async()=>view.unmount());
});

test('company conversation hides personal key and endpoint without deleting saved settings', async () => {
  const writes=[];const View=harness({state:{settings:{mode:'offline',offlineBaseUrl:'http://127.0.0.1:8000/v1',offlineApiKey:'old-key',workspace:'test'},updateSettings:async v=>writes.push(v)}}).load('views/SettingsView.tsx').default;
  let view;await act(async()=>{view=create(React.createElement(View));await settle();});
  assert.equal(view.root.findAllByProps({id:'settings-pane-model'}).length,0);
  assert.equal(view.root.findAllByProps({id:'settings-tab-model'}).length,0);assert.deepEqual(writes,[]);
  await act(async()=>view.unmount());
});

test('settings navigation shows one category and preserves unsaved connection edits', async () => {
  const View=harness({state:{settings:{mode:'offline',workspace:'test'}}}).load('views/SettingsView.tsx').default;
  let view;await act(async()=>{view=create(React.createElement(View));await settle();});
  const panel=id=>view.root.findByProps({id:'settings-pane-'+id});
  const tab=id=>view.root.findByProps({id:'settings-tab-'+id});
  assert.equal(!!panel('account').props.hidden,false);assert.equal(panel('tools').props.hidden,true);
  await act(async()=>tab('tools').props.onClick());
  assert.equal(view.root.findAllByProps({id:'settings-pane-account'}).length,0);assert.equal(panel('tools').props.hidden,false);
  await act(async()=>tab('account').props.onClick());
  assert.equal(panel('account').findAllByType('input').length,0);
  await act(async()=>tab('workspace').props.onClick());await act(async()=>tab('account').props.onClick());
  assert.equal(view.root.findAllByProps({id:'settings-tab-model'}).length,0);
  assert.equal(view.root.findAllByProps({role:'tabpanel'}).filter(n=>!n.props.hidden).length,1);
  await act(async()=>view.unmount());
});

test('about and workspace omit legacy path text but open the real unchanged data directory', async()=>{
  const paths=[];
  const View=harness({state:{settings:{mode:'online',mcpServers:[],workspace:'C:/Users/test/BioDSH/workspace'},info:{version:'test',paths:{root:'C:/Users/test/BioDSH'}}},api:{openPath:p=>paths.push(p)}}).load('views/SettingsView.tsx').default;
  let view;await act(async()=>{view=create(React.createElement(View));await settle();});
  assert.doesNotMatch(text(view.root),/BioDSH|bioDSH/);
  await act(async()=>view.root.findAllByType('button').find(b=>text(b).trim()==='打开数据目录').props.onClick());
  assert.deepEqual(paths,['C:/Users/test/BioDSH']);
  assert.match(text(view.root),/原始版权和 MIT 许可证随软件保留/);
  await act(async()=>view.unmount());
});

test('local environment is not installed on opening and remains an explicit action', async () => {
  let installs = 0;
  const View = harness({ state: { installEnv: () => installs++ } }).load('views/EnvView.tsx').default;
  let view; await act(async () => { view = create(React.createElement(View)); });
  assert.equal(installs, 0); assert.match(text(view.root), /两边的环境不会自动共用/);
  await act(async () => view.root.findAllByType('button').find(b => text(b).trim() === '准备本机环境').props.onClick());
  assert.equal(installs, 1); await act(async () => view.unmount());
});

test('opening tool catalog does not install software or demand a local environment', async () => {
  let installs = 0;
  const View = harness({ api: { skillsInstall: () => installs++ } }).load('views/StoreView.tsx').default;
  let view; await act(async () => { view = create(React.createElement(View)); await settle(); });
  assert.equal(installs, 0);
  assert.match(text(view.root), /只浏览或添加工具说明，不需要先安装环境/);
  assert.doesNotMatch(text(view.root), /全部.*已评测/); await act(async () => view.unmount());
});

test('resource cards separate server usage from task limits and retain metrics', async () => {
  const View = harness().load('views/HostsView.tsx').ResourceSummary;
  const value = { cpuPercent: 25, logicalCores: 40, physicalCores: 20, cpuModel: 'Test CPU', memoryUsed: 1024 ** 3, memoryTotal: 4 * 1024 ** 3, swapUsed: 0, swapTotal: 0, disks: [], sampledAt: 1, hostname: 'test', system: 'Linux', architecture: 'x86_64', uptimeSeconds: 86400, load: [1, 2, 3] };
  let view; await act(async () => { view = create(React.createElement(View, { value })); });
  assert.match(text(view.root), /不是本次任务的配额/);
  assert.equal(view.root.findAllByProps({ role: 'meter' }).length, 3);
  assert.match(text(view.root), /交换空间已有占用，不一定表示服务器当前缺内存/);
  await act(async () => view.unmount());
});

test('settings model usage retains current, project and total counts including cached tokens', async () => {
  const session=(id,input,output,cache)=>({sessionId:id,projections:{values:{tokenUsage:{uncachedInputTokens:input,outputTokens:output,cacheReadTokens:cache}}}});
  const View=harness({state:{currentSession:'a',dsh:{state:'running'}},api:{
    deepseekBalance:async()=>({balance_infos:[{currency:'CNY',total_balance:'12.50'}]}),
    dshRpc:async method=>method==='workspace.list'?{items:[{sessionIds:['a','b']}]}:{items:[session('a',10,20,30),session('b',1,2,3),session('c',100,200,300)]},
  }}).load('components/ModelUsage.tsx').default;
  let view;await act(async()=>{view=create(React.createElement(View));await settle();});
  assert.doesNotMatch(text(view.root),/DeepSeek|CNY 12.50/);assert.match(text(view.root),/本机保存的对话用量/);
  assert.deepEqual(view.root.findAllByType('dd').map(text),['60 tokens','66 tokens','666 tokens']);
  await act(async()=>view.unmount());
});
