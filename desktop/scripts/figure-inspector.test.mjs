import {after,afterEach,before,beforeEach,test} from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {build} from 'esbuild';

const root=fileURLToPath(new URL('../',import.meta.url)),require=createRequire(import.meta.url);
let playwright;try{playwright=require('playwright');}catch{playwright=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json'))('playwright');}
const svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 180"><g data-vector-id="group-a" transform="translate(5 8) scale(2)"><text data-vector-id="text-a" x="5" y="20" font-size="12">A</text></g><g data-vector-id="group-b" transform="translate(50 10) rotate(17) scale(.6 1.7)"><text data-vector-id="text-b" x="8" y="45" font-size="11">B<tspan data-vector-id="span-b" x="8" y="60" font-size="7">B2</tspan></text></g><text data-vector-id="text-c" x="8" y="140" font-size="10">C</text><g data-vector-id="locked-group" data-vector-locked="true"><text data-vector-id="locked-child" x="80" y="100" font-size="13">Locked child</text></g><g data-vector-id="hidden-group" display="none"><text data-vector-id="hidden-child" x="80" y="120" font-size="13">Hidden child</text></g><rect data-vector-id="own-hidden" x="2" y="2" width="10" height="10" display="none"/></svg>';
const seed={format:'omaax-figure',version:1,title:'Font scaling',width:800,height:600,widthMm:180,items:[{id:'panel',name:'Panel',kind:'svg',svg,x:60,y:40,width:440,height:300,rotation:23,preserveAspectRatio:'none',hidden:false,locked:false}]};
let browser,page,bundle,errors;
before(async()=>{
  const entry=`import React,{useState} from 'react';import{createRoot}from'react-dom/client';import FigureInspector from './src/renderer/src/views/FigureInspector';import{figureSvg}from'./src/renderer/src/views/figure-model';import{vectorObjects,editVector}from'./src/renderer/src/views/figure-vector';import{LangContext}from'./src/renderer/src/i18n';
  window.qa={batches:[],vectors:[],selections:[]};
  function App(){const[doc,setDoc]=useState(${JSON.stringify(seed)}),[nodes,setNodes]=useState(['text-a','text-b']),[busy,setBusy]=useState(false);window.qa.doc=doc;window.qa.configure=value=>{if(value.nodes)setNodes(value.nodes);if('busy'in value)setBusy(value.busy);if('locked'in value||'hidden'in value)setDoc(d=>({...d,items:d.items.map(i=>({...i,...('locked'in value?{locked:value.locked}:{}),...('hidden'in value?{hidden:value.hidden}:{})}))}));};const current=doc.items[0];
  function batch(changes){window.qa.batches.push(changes);let svg=current.svg;for(const change of changes)svg=editVector(svg,change.ids,change.patch);setDoc({...doc,items:[{...current,svg}]});}
  return <><div className="figure-artwork" style={{width:800,height:600,transform:'scale(.8)',transformOrigin:'top left'}} dangerouslySetInnerHTML={{__html:figureSvg(doc)}}/><FigureInspector doc={doc} current={current} inside="panel" nodes={nodes} objects={vectorObjects(current.svg)} fonts={['Arial']} busy={busy} onDocument={setDoc} onStyle={()=>{}} onItem={()=>{}} onVector={(patch,ids)=>window.qa.vectors.push({patch,ids})} onVectorBatch={batch} onNodes={ids=>{window.qa.selections.push(ids);setNodes(ids)}}/></>}
  createRoot(document.getElementById('root')).render(<LangContext.Provider value="zh"><App/></LangContext.Provider>);`;
  const built=await build({stdin:{contents:entry,resolveDir:root,loader:'tsx'},write:false,bundle:true,format:'iife',platform:'browser',jsx:'automatic',define:{'import.meta.env.DEV':'false'}});
  bundle=built.outputFiles[0].text;browser=await playwright.chromium.launch({channel:'msedge',headless:true});
});
beforeEach(async()=>{errors=[];page=await browser.newPage({viewport:{width:1400,height:1100}});page.on('pageerror',error=>errors.push(String(error)));await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html><body><div id="root"></div></body></html>'}));await page.goto('http://inspector.test');await page.addScriptTag({content:bundle});await page.getByLabel('印刷字号（pt）',{exact:true}).waitFor();});
afterEach(async()=>{await page.close();assert.deepEqual(errors,[]);});
after(async()=>{await browser?.close();});
async function settled(){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function pointSizes(ids){return page.evaluate(ids=>{const root=document.querySelector('.figure-artwork>svg'),base=root.getScreenCTM(),doc=window.qa.doc;return ids.map(id=>{const el=root.querySelector('[data-vector-id="'+id+'"]'),m=base.inverse().multiply(el.getScreenCTM());return Number(getComputedStyle(el).fontSize.replace('px',''))*Math.hypot(m.c,m.d)*doc.widthMm/doc.width/25.4*72;});},ids);}

test('multi-selection converts each nested text scale and applies one batch',async()=>{
  const cBefore=(await pointSizes(['text-c']))[0];
  await page.getByLabel('印刷字号（pt）',{exact:true}).fill('9.5');await settled();
  for(const size of await pointSizes(['text-a','text-b','span-b']))assert.ok(Math.abs(size-9.5)<.0001,String(size));
  assert.equal((await pointSizes(['text-c']))[0],cBefore);
  const batches=await page.evaluate(()=>window.qa.batches);assert.equal(batches.length,1);assert.equal(batches[0].length,3);
  assert.notEqual(batches[0][0].patch.fontSize,batches[0][1].patch.fontSize);
});

test('all-text scope preserves locked and hidden subtrees while normalizing visible text',async()=>{
  const before=await page.evaluate(()=>window.qa.doc.items[0].svg);
  await page.getByLabel('修改范围',{exact:true}).selectOption('text');
  await page.getByLabel('印刷字号（pt）',{exact:true}).fill('12');await settled();
  for(const size of await pointSizes(['text-a','text-b','span-b','text-c']))assert.ok(Math.abs(size-12)<.0001,String(size));
  const ids=await page.evaluate(()=>window.qa.batches[0].flatMap(change=>change.ids));assert.ok(!ids.includes('locked-child'));assert.ok(!ids.includes('hidden-child'));
  const sizes=await page.evaluate(()=>{const doc=new DOMParser().parseFromString(window.qa.doc.items[0].svg,'image/svg+xml');return ['locked-child','hidden-child'].map(id=>doc.querySelector('[data-vector-id="'+id+'"]').getAttribute('font-size'));});
  assert.deepEqual(sizes,['13','13']);assert.ok(before.includes('font-size="13"'));
});

test('object-tree actions cannot edit children of locked or hidden parents',async()=>{
  const locked=page.locator('.figure-object-tree>div').filter({has:page.locator('button[title="Locked child"]')});
  const hidden=page.locator('.figure-object-tree>div').filter({has:page.locator('button[title="Hidden child"]')});
  for(const row of [locked,hidden])assert.equal(await row.locator('button:enabled').count(),0);
  // The locked group itself can still be unlocked; only its descendants are protected.
  assert.equal(await page.getByLabel('解锁对象',{exact:true}).first().isEnabled(),true);
  await locked.locator('button').evaluateAll(buttons=>buttons.forEach(button=>button.click()));
  assert.deepEqual(await page.evaluate(()=>window.qa.vectors),[]);
});

test('busy, locked panel and hidden panel disable every object-tree action',async()=>{
  for(const configuration of [{busy:true},{busy:false,locked:true},{locked:false,hidden:true}]){
    await page.evaluate(value=>window.qa.configure(value),configuration);await settled();
    assert.equal(await page.locator('.figure-object-tree button:enabled').count(),0);
    assert.equal(await page.getByLabel('印刷字号（pt）',{exact:true}).isDisabled(),true);
  }
});
