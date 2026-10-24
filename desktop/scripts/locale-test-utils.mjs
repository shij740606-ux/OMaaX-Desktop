import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = fileURLToPath(new URL('../', import.meta.url));
const realRequire = createRequire(import.meta.url);
const React = realRequire('react');
const { act, create } = realRequire('react-test-renderer');
const settle = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const text = node => typeof node === 'string' ? node : (Array.isArray(node) ? node : node?.children || []).map(text).join(' ');
const han = /[\p{Script=Han}]/u;
function fixture({ state = {}, api = {}, auth = {} } = {}) {
  const modules = new Map(), events = new EventTarget(), storage = new Map();
  const app = { settings: { language:'en', theme:'light', workspace:'C:/Project', mcpServers:[], onboarded:true }, ready:true, info: {version:'0.4.50',platform:'win32',paths:{root:'C:/Data'}}, dsh:{state:'stopped',log:[]}, env:{ready:false,step:'idle',log:[],message:''}, tab:'settings', currentSession:null, statuses:{}, busy:{}, catalog:[], credential:{}, platform:{authenticated:false,projects:[]}, updateSettings:async()=>{}, restartDsh:async()=>{}, setTab:()=>{}, ...state };
  globalThis.localStorage = globalThis.sessionStorage = {getItem:key=>storage.get(key)||null, setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
  globalThis.document = {documentElement:{lang:'en'},getElementById:()=>null};
  globalThis.window = {addEventListener:events.addEventListener.bind(events),removeEventListener:events.removeEventListener.bind(events),dispatchEvent:events.dispatchEvent.bind(events),setTimeout:()=>0,matchMedia:()=>({matches:false}),biodsh:{getSettings:async()=>app.settings,onEvent:()=>()=>{},hubStatus:async()=>({authenticated:false,hub:''}),hubRememberedLogin:async()=>null,dshSetContext:async()=>{},gitStatus:async()=>({available:false}),dshRpc:async(method)=>method==='workspace.list'?{items:[],archivedSessionIds:[]}:{items:[]},...api}};
  const load = file => {
    if (modules.has(file)) return modules.get(file).exports;
    const module = {exports:{}}; modules.set(file,module);
    if(file.endsWith('.json'))return module.exports=JSON.parse(fs.readFileSync(file,'utf8'));
    let source=fs.readFileSync(file,'utf8').replace(/import\.meta\.env\??\.DEV/g,'false');
    const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
    const require = name => {
      if(name.endsWith('.css'))return {};
      if(/\.(png|jpe?g|svg|mp4)$/.test(name))return {__esModule:true,default:name};
      // Playback timing has its own suite; locale tests exercise mounted application content.
      if(name.endsWith('/StartupIntro'))return {__esModule:true,default:({children})=>children};
      if(name==='lucide-react')return new Proxy({},{get:(_,key)=>key==='__esModule'?false:()=>null});
      if(name.endsWith('/store'))return {useApp:()=>app};
      if(name.endsWith('/AuthGate'))return {useAuth:()=>({user:{username:'researcher',role:'admin',authorizations:['qa']},...auth}),hasUnit:()=>true};
      if(name.endsWith('/accountMock'))return {loadAccountOverview:async()=>({planId:'basic',usage:{usedTokens:0,quotaTokens:1,callCount:0,costCny:0,resetAt:'2026-11-01'},plans:[],records:[]})};
      if(name.startsWith('.')){const base=path.resolve(path.dirname(file),name);const next=['','.ts','.tsx'].map(ext=>base+ext).find(p=>fs.existsSync(p)&&fs.statSync(p).isFile());assert.ok(next,name);return load(next);}
      return realRequire(name);
    };
    new Function('require','module','exports','setInterval','clearInterval','setTimeout',code)(require,module,module.exports,()=>0,()=>{},()=>0);
    return module.exports;
  };
  const get=file=>load(path.join(root,'src/renderer/src',file));
  return {get, app, wrap:(Component,lang='en',props={})=>React.createElement(get('i18n.ts').LangContext.Provider,{value:lang},React.createElement(Component,props))};
}
function assertEnglish(view, allowed=[]){
  let content=text(view.toJSON());for(const value of allowed)content=content.replaceAll(value,'');
  assert.doesNotMatch(content,han,content);
  for(const item of view.root.findAll(n=>typeof n.type==='string'))for(const key of ['title','placeholder','aria-label'])if(typeof item.props[key]==='string'){
    let value=item.props[key];for(const allow of allowed)value=value.replaceAll(allow,'');assert.doesNotMatch(value,han,`${key}: ${value}`);
  }
}


export {root,React,act,create,settle,text,han,fixture,assertEnglish};
