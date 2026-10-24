import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url);
const Context=React.createContext(null);
const code=ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/views/EducationView.tsx',import.meta.url),'utf8'),{
 compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX},
}).outputText;
const module={exports:{}};
new Function('require','exports',code)(name=>name==='../store'?{useApp:()=>React.useContext(Context)}:name==='../i18n'?{useT:()=>({lang:React.useContext(Context).settings.language,t:s=>s})}:require(name),module.exports);
const View=module.exports.default;
const settle=async()=>{for(let i=0;i<6;i++)await Promise.resolve();};
const url='http://127.0.0.1:8101/test-capability/index.html';
const localizedUrl=(value,lang='zh')=>`${value}?language=${lang}`;
function setup(start=async()=>url){
 let renderer,calls=0;
 const listeners=new Set();
 globalThis.window={biodsh:{educationStart:()=>{calls++;return start();}},
  addEventListener:(_,f)=>listeners.add(f),removeEventListener:(_,f)=>listeners.delete(f)};
 const state={settings:{theme:'light',language:'zh'},setTab:()=>{}};
 const tree=active=>React.createElement(Context.Provider,{value:state},React.createElement(View,{active}));
 return {
  async mount(active){await act(async()=>{renderer=create(tree(active));await settle();});},
  async show(active){await act(async()=>{renderer.update(tree(active));await settle();});},
  async language(value){state.settings={...state.settings,language:value};await act(async()=>{renderer.update(tree(true));await settle();});},
  async click(label){await act(async()=>{renderer.root.findAllByType('button').find(b=>b.children.includes(label)).props.onClick();await settle();});},
  async stop(){await act(async()=>renderer.unmount());assert.equal(listeners.size,0);},
  get calls(){return calls;},get view(){return renderer.root;},
 };
}

test('starts lazily, and 100 round trips retain the same iframe without new startup calls',async()=>{
 const s=setup();await s.mount(false);assert.equal(s.calls,0);
 await s.show(true);assert.equal(s.calls,1);const frame=s.view.findByType('iframe');
 for(let i=0;i<100;i++){await s.show(false);await s.show(true);}
 assert.equal(s.calls,1);assert.equal(s.view.findByType('iframe'),frame);assert.equal(frame.props.src,localizedUrl(url));
 await s.stop();
});

test('startup finishes while hidden, without being discarded or duplicated',async()=>{
 let resolve;const s=setup(()=>new Promise(r=>resolve=r));await s.mount(true);await s.show(false);
 await s.show(true);await s.show(false);assert.equal(s.calls,1);
 await act(async()=>{resolve(url);await settle();});
 assert.equal(s.view.findByType('iframe').props.src,localizedUrl(url));await s.show(true);assert.equal(s.calls,1);
 await s.stop();
});

test('startup failure remains retryable after leaving and returning',async()=>{
 let reject;const s=setup(()=>s.calls===1?new Promise((_,r)=>reject=r):Promise.resolve(url));
 await s.mount(true);await s.show(false);
 await act(async()=>{reject(Error('暂时无法启动'));await settle();});await s.show(true);
 assert.equal(s.calls,1);assert.match(s.view.findByProps({role:'alert'}).children.join(''),/暂时无法启动/);
 await s.click('重试');assert.equal(s.calls,2);assert.equal(s.view.findByType('iframe').props.src,localizedUrl(url));
 await s.stop();
});

test('explicit reload obtains a fresh service URL, and normal navigation still reuses it',async()=>{
 const next=url.replace(':8101',':8102');const s=setup(async()=>s.calls===1?url:next);await s.mount(true);
 await s.click('重新加载');assert.equal(s.calls,2);assert.equal(s.view.findByType('iframe').props.src,localizedUrl(next));
 await s.show(false);await s.show(true);assert.equal(s.calls,2);await s.stop();
});

test('unmount ignores a late response and removes message listeners',async()=>{
 let resolve;const s=setup(()=>new Promise(r=>resolve=r));await s.mount(true);await s.stop();
 await act(async()=>{resolve(url);await settle();});assert.equal(s.calls,1);
});

test('changing language keeps the iframe URL and live workspace intact',async()=>{
 const s=setup();await s.mount(true);const frame=s.view.findByType('iframe');const src=frame.props.src;
 await s.language('en');assert.equal(s.calls,1);assert.equal(s.view.findByType('iframe'),frame);assert.equal(frame.props.src,src);
 await s.language('zh');assert.equal(s.calls,1);assert.equal(frame.props.src,src);await s.stop();
});

test('startup uses the latest language even when the setting changes during preparation',async()=>{
 let resolve;const s=setup(()=>new Promise(r=>resolve=r));await s.mount(true);await s.language('en');
 await act(async()=>{resolve(url);await settle();});assert.equal(s.view.findByType('iframe').props.src,localizedUrl(url,'en'));assert.equal(s.calls,1);await s.stop();
});
