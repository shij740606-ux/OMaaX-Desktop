import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import ts from 'typescript';

const require=createRequire(import.meta.url);
let playwright;
try{playwright=require('playwright');}catch{playwright=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json'))('playwright');}
const source=ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/views/figure-draft.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
let browser;
before(async()=>{browser=await playwright.chromium.launch({channel:'msedge',headless:true});});
after(async()=>{await browser?.close();});
async function scenario(work){
  const context=await browser.newContext(),page=await context.newPage();
  await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body>Draft test</body></html>'}));
  await page.goto('http://figure-draft.test');
  await page.evaluate(code=>{window.installDraft=()=>{const module={exports:{}};new Function('module','exports',code)(module,module.exports);window.draft=module.exports;};window.installDraft();},source);
  try{return await work(page);}finally{await context.close();}
}

test('drafts larger than localStorage quota persist and survive module reload',async()=>scenario(async page=>{
  const result=await page.evaluate(async()=>{
    const json=JSON.stringify({document:'x'.repeat(7*1024*1024)});
    await window.draft.saveFigureDraft('omaax.figure.large',json);
    window.installDraft();
    const loaded=await window.draft.loadFigureDraft('omaax.figure.large');
    return {equal:loaded===json,bytes:loaded.length,legacy:localStorage.getItem('omaax.figure.large')};
  });
  assert.equal(result.equal,true);assert.ok(result.bytes>5*1024*1024);assert.equal(result.legacy,null);
}));

test('legacy drafts migrate; two accounts, ordered writes and deletion stay independent',async()=>scenario(async page=>{
  const result=await page.evaluate(async()=>{
    localStorage.setItem('alice','legacy-alice');
    const legacy=await window.draft.loadFigureDraft('alice');
    const migrated=localStorage.getItem('alice');
    await Promise.all([window.draft.saveFigureDraft('alice','first'),window.draft.saveFigureDraft('alice','last'),window.draft.saveFigureDraft('bob','bob-content')]);
    const last=await window.draft.loadFigureDraft('alice');
    await window.draft.deleteFigureDraft('alice');
    return {legacy,migrated,last,deleted:await window.draft.loadFigureDraft('alice'),bob:await window.draft.loadFigureDraft('bob')};
  });
  assert.deepEqual(result,{legacy:'legacy-alice',migrated:null,last:'last',deleted:null,bob:'bob-content'});
}));

test('fallback updates and deletions supersede an older IndexedDB draft on recovery',async()=>scenario(async page=>{
  const result=await page.evaluate(async()=>{
    await window.draft.saveFigureDraft('alice','old-idb');
    const factory=window.indexedDB;
    Object.defineProperty(window,'indexedDB',{configurable:true,value:undefined});window.installDraft();
    await window.draft.saveFigureDraft('alice','new-fallback');
    Object.defineProperty(window,'indexedDB',{configurable:true,value:factory});window.installDraft();
    const recovered=await window.draft.loadFigureDraft('alice');
    Object.defineProperty(window,'indexedDB',{configurable:true,value:undefined});window.installDraft();
    await window.draft.deleteFigureDraft('alice');
    Object.defineProperty(window,'indexedDB',{configurable:true,value:factory});window.installDraft();
    return {recovered,deleted:await window.draft.loadFigureDraft('alice')};
  });
  assert.deepEqual(result,{recovered:'new-fallback',deleted:null});
}));

test('transaction failures report errors, preserve the prior draft and allow a later retry',async()=>scenario(async page=>{
  const result=await page.evaluate(async()=>{
    await window.draft.saveFigureDraft('alice','previous');
    const put=IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put=function(...args){const result=put.apply(this,args);this.transaction.abort();return result;};
    let error='';try{await window.draft.saveFigureDraft('alice','failed');}catch(reason){error=reason.message;}
    IDBObjectStore.prototype.put=put;
    const retained=await window.draft.loadFigureDraft('alice');
    await window.draft.saveFigureDraft('alice','retry');
    return {error,retained,retried:await window.draft.loadFigureDraft('alice')};
  });
  assert.match(result.error,/草稿读写失败/);assert.equal(result.retained,'previous');assert.equal(result.retried,'retry');
}));

test('fallback quota errors reject instead of reporting a successful save',async()=>scenario(async page=>{
  const message=await page.evaluate(async()=>{
    Object.defineProperty(window,'indexedDB',{configurable:true,value:undefined});window.installDraft();
    Storage.prototype.setItem=function(){throw new DOMException('full','QuotaExceededError');};
    try{await window.draft.saveFigureDraft('alice','content');return 'unexpected success';}catch(error){return error.message;}
  });
  assert.match(message,/存储空间不足/);
}));
