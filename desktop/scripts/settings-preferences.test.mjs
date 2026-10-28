import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
const desktop=fileURLToPath(new URL('../',import.meta.url));
const require=createRequire(import.meta.url);
let playwright;try{playwright=require('playwright');}catch{playwright=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json'))('playwright');}

test('appearance controls serialize requests, recover saved preferences on failure, and preserve the current page',async()=>{
  const entry=`import React from 'react';import{createRoot}from'react-dom/client';import SettingsView from './src/renderer/src/views/SettingsView';import{AppProvider,useApp}from'./src/renderer/src/store';import{LangContext}from'./src/renderer/src/i18n';
const saved={theme:'light',language:'zh',mcpServers:[],workspace:'fixture',onboarded:true};
const state=window.fixture={writes:[],reads:0,restarts:0,errors:[]};window.addEventListener('unhandledrejection',e=>state.errors.push(String(e.reason)));
window.biodsh={getSettings:async()=>{state.reads++;return {...saved};},setSettings:async patch=>{state.writes.push(patch);Object.assign(saved,patch);return new Promise((resolve,reject)=>state.complete=ok=>ok?resolve({...saved}):reject(new Error('Synthetic private provider diagnostic')));},appInfo:async()=>({version:'0.4.60',paths:{}}),getCredential:async()=>({hasKey:false}),envStatus:async()=>({ready:true}),dshStatus:async()=>({state:'running',log:[]}),skillsCatalog:async()=>[],skillsStatuses:async()=>[],onEvent:()=>()=>{},onDebugTab:()=>()=>{},dshRestart:async()=>{state.restarts++;return{state:'running'};}};
function Snapshot(){const{settings}=useApp();return <output data-testid="settings">{JSON.stringify(settings)}</output>}
createRoot(document.getElementById('root')).render(<LangContext.Provider value="zh"><AppProvider><textarea aria-label="draft" defaultValue="尚未发送的研究问题"/><Snapshot/><SettingsView initialPane="appearance"/></AppProvider></LangContext.Provider>);`;
  const built=await build({stdin:{contents:entry,resolveDir:desktop,loader:'tsx'},bundle:true,write:false,outdir:'settings-test',jsx:'automatic',platform:'browser',define:{'import.meta.env.DEV':'false'},plugins:[{name:'test-account',setup(b){b.onResolve({filter:/AuthGate$/},()=>({path:'account',namespace:'mock'}));b.onLoad({filter:/.*/,namespace:'mock'},()=>({contents:'export const useAuth=()=>({user:{id:"settings-fixture"}});'}));}}]});
  const js=built.outputFiles.find(file=>file.path.endsWith('.js')).text,css=built.outputFiles.find(file=>file.path.endsWith('.css'))?.text||'';
  const browser=await playwright.chromium.launch({channel:'msedge',headless:true});
  const evidence=path.join(desktop,'../validation-local/settings-preferences');fs.mkdirSync(evidence,{recursive:true});
  try {
    const page=await browser.newPage({viewport:{width:1200,height:900}}),errors=[];let navigations=0;
    page.on('pageerror',e=>errors.push(String(e)));page.on('framenavigated',frame=>{if(frame===page.mainFrame())navigations++;});
    await page.route('**/*',r=>r.fulfill({contentType:'text/html',body:`<!doctype html><html><head><style>${css}[hidden]{display:none}button{margin:4px}button.active{background:#dbeafe}body{font:14px Arial}textarea{width:80%}</style></head><body><div id="root"></div></body></html>`}));
    await page.goto('http://settings-preferences.test');await page.addScriptTag({content:js});
    const appearance=page.locator('[data-pane="appearance"]'),dark=appearance.getByRole('button',{name:'深色',exact:true}),english=appearance.getByRole('button',{name:'English',exact:true});
    await dark.waitFor();await dark.click();
    await page.waitForFunction(()=>document.querySelectorAll('[data-pane="appearance"] .seg button:disabled').length===6);
    await english.evaluate(button=>button.click());
    assert.equal(await page.evaluate(()=>window.fixture.writes.length),1,'disabled language controls must not send an overlapping request');
    await page.evaluate(()=>window.fixture.complete(false));
    await appearance.getByRole('alert').waitFor();
    assert.match(await appearance.getByRole('alert').innerText(),/界面设置未完全同步/);
    assert.doesNotMatch(await appearance.getByRole('alert').innerText(),/provider|Synthetic|deepseek|harness/i);
    await page.waitForFunction(()=>document.querySelector('[data-testid="settings"]').textContent.includes('"theme":"dark"'));
    assert.match(await dark.getAttribute('class'),/active/);assert.equal(await dark.isEnabled(),true);
    assert.equal(await page.evaluate(()=>window.fixture.reads),2,'read the saved host state after a partial synchronization failure');
    await page.screenshot({path:path.join(evidence,'visible-error.png')});

    await dark.click();assert.equal(await appearance.getByRole('alert').count(),0);await page.evaluate(()=>window.fixture.complete(true));
    await page.waitForFunction(()=>document.querySelectorAll('[data-pane="appearance"] .seg button:disabled').length===0);
    await english.click();await page.evaluate(()=>window.fixture.complete(true));
    await page.waitForFunction(()=>document.querySelector('[data-testid="settings"]').textContent.includes('"language":"en"'));
    assert.match(await english.getAttribute('class'),/active/);
    assert.equal(await page.getByLabel('draft',{exact:true}).inputValue(),'尚未发送的研究问题');assert.equal(navigations,1);
    assert.equal(await page.evaluate(()=>window.fixture.restarts),0);assert.deepEqual(await page.evaluate(()=>window.fixture.errors),[]);assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify({serializedRequests:true,savedStateRecovered:true,visibleChineseError:true,retrySucceeded:true,languageSucceeded:true,pageReloads:0,assistantRestarts:0,unhandledRejections:0},null,2));
  }finally{await browser.close();}
});
