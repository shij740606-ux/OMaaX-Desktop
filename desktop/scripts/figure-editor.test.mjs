import {after, afterEach, before, beforeEach, test} from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {build} from 'esbuild';

const root=fileURLToPath(new URL('../',import.meta.url));
const require=createRequire(import.meta.url);
let playwright;
try{playwright=require('playwright');}
catch{playwright=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json'))('playwright');}
const image='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5E3yQAAAAASUVORK5CYII=';
const seed={format:'omaax-figure',version:1,title:'Alice original',width:700,height:500,widthMm:180,items:[
  {id:'editable',name:'Editable panel',kind:'svg',x:30,y:40,width:200,height:150,rotation:12,hidden:false,locked:false,groupId:'keep-group',svg:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 150"><text x="10" y="20">Original chart</text></svg>'},
  {id:'hidden',name:'Hidden panel',kind:'rect',x:250,y:40,width:120,height:120,rotation:0,hidden:true,locked:false,fill:'#abcdef',stroke:'#112233'},
  {id:'locked',name:'Locked panel',kind:'text',x:30,y:240,width:200,height:50,rotation:0,hidden:false,locked:true,text:'Protected annotation',fontSize:24,color:'#123456'},
  {id:'raster',name:'Original image',kind:'image',x:350,y:250,width:80,height:60,rotation:0,hidden:false,locked:false,dataUrl:image},
]};
const update='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 150"><text x="10" y="20">Updated chart</text></svg>';
let browser,page,bundle,errors,requests;

before(async()=>{
  // Keep the editor, model and translations real; replace only the native bridge and account source.
  const entry=`import React from 'react';import{createRoot}from'react-dom/client';import FigureEditor from './src/renderer/src/views/FigureEditor';import{LangContext}from'./src/renderer/src/i18n';
    window.__qa={owner:'alice',pending:[],cancellations:[],calls:[],deferCancel:false};
    window.__qa.setOwner=id=>{window.__qa.owner=id;window.dispatchEvent(new Event('qa-owner'));};
    window.biodsh={dshRpc:async()=>({items:[]}),pickFolder:async()=>null,drawing:(action,body)=>{
      window.__qa.calls.push({action,body});
      if(action==='figure_generate')return new Promise((resolve,reject)=>window.__qa.pending.push({resolve,reject}));
      if(action==='figure_cancel'&&window.__qa.deferCancel)return new Promise((resolve,reject)=>window.__qa.cancellations.push({resolve,reject}));
      return Promise.resolve({ok:true});
    }};
    localStorage.setItem('omaax.figure.alice',JSON.stringify({document:${JSON.stringify(seed)},history:[]}));
    createRoot(document.getElementById('root')).render(<LangContext.Provider value="zh"><FigureEditor active/></LangContext.Provider>);`;
  const built=await build({stdin:{contents:entry,resolveDir:root,loader:'tsx'},write:false,bundle:true,platform:'browser',format:'iife',jsx:'automatic',define:{'import.meta.env.DEV':'false'},loader:{'.css':'empty'},plugins:[{
    name:'figure-test-account',setup(builder){
      builder.onResolve({filter:/\/AuthGate$/},()=>({path:'auth',namespace:'figure-test-account'}));
      builder.onLoad({filter:/.*/,namespace:'figure-test-account'},()=>({
        contents:"import{useEffect,useState}from'react';export function useAuth(){const[id,setId]=useState(window.__qa.owner);useEffect(()=>{const listener=()=>setId(window.__qa.owner);window.addEventListener('qa-owner',listener);return()=>window.removeEventListener('qa-owner',listener)},[]);return{user:{id}};}",
        loader:'js',resolveDir:root,
      }));
    },
  }]});
  bundle=built.outputFiles[0].text;
  browser=await playwright.chromium.launch({headless:true,channel:'msedge'});
});
beforeEach(async()=>{
  errors=[];requests=[];
  page=await browser.newPage({viewport:{width:1500,height:1000}});
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/*',route=>{
    if(route.request().url()==='http://127.0.0.1/figure-test')return route.fulfill({contentType:'text/html',body:'<!DOCTYPE html><html><body><div id="root"></div></body></html>'});
    requests.push(route.request().url());return route.abort();
  });
  await page.goto('http://127.0.0.1/figure-test');await page.addScriptTag({content:bundle});
  await page.getByRole('button',{name:'Editable panel',exact:true}).waitFor();
});
afterEach(async()=>{await page?.close();assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);});
after(async()=>{await browser?.close();});

const saved=(owner='alice')=>page.evaluate(id=>JSON.parse(localStorage.getItem('omaax.figure.'+id)).document,owner);
const modelCalls=()=>page.evaluate(()=>window.__qa.calls.filter(c=>c.action==='figure_generate'));
async function generate(name='Editable panel'){
  await page.getByRole('button',{name,exact:true}).click();
  await page.getByLabel('拼图修改要求',{exact:true}).fill('Unify typography without changing the source data');
  await page.getByRole('button',{name:'修改图形',exact:true}).click();
}
async function resolve(index,svg=update){
  await page.evaluate(async({index,svg})=>{
    window.__qa.pending[index].resolve({svg});
    await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));
  },{index,svg});
}
async function waitUpdated(){
  await page.waitForFunction(()=>JSON.parse(localStorage.getItem('omaax.figure.alice')).document.items[0].svg.includes('Updated chart'));
}

test('stopping ignores a late model response and retains the current drawing',async()=>{
  await generate();await page.waitForFunction(()=>window.__qa.pending.length===1);
  await page.getByRole('button',{name:'停止',exact:true}).click();await resolve(0);
  assert.match((await saved()).items[0].svg,/Original chart/);
  assert.equal(await page.getByRole('status').textContent(),'已停止，保留修改前的图。');
  const cancel=await page.evaluate(()=>window.__qa.calls.filter(c=>c.action==='figure_cancel'));
  assert.equal(cancel.length,1);assert.equal(cancel[0].body.requestId,(await modelCalls())[0].body.requestId);
});
test('switching accounts cancels the previous request and rejects its late result',async()=>{
  await generate();await page.waitForFunction(()=>window.__qa.pending.length===1);
  await page.evaluate(()=>window.__qa.setOwner('bob'));
  await page.getByText('把图表放到这里，开始拼图',{exact:true}).waitFor();await resolve(0);
  await page.waitForFunction(()=>localStorage.getItem('omaax.figure.bob')!==null);
  assert.equal((await saved('bob')).items.length,0);assert.match((await saved()).items[0].svg,/Original chart/);
  assert.equal(await page.evaluate(()=>window.__qa.calls.filter(c=>c.action==='figure_cancel').length),1);
});
test('model refinement preserves other layers and the selected panel geometry and grouping',async()=>{
  await generate();await page.waitForFunction(()=>window.__qa.pending.length===1);await resolve(0);await waitUpdated();
  const result=await saved();assert.equal(result.items.length,seed.items.length);
  for(const key of ['id','name','x','y','width','height','rotation','groupId'])assert.equal(result.items[0][key],seed.items[0][key],key);
  assert.deepEqual(result.items.slice(1),seed.items.slice(1));
  const input=(await modelCalls())[0].body.svg;
  assert.match(input,/Original chart/);assert.doesNotMatch(input,/Protected annotation|data:image\/png/);
});
test('model refinement supports undo and redo without altering the original panels',async()=>{
  await generate();await page.waitForFunction(()=>window.__qa.pending.length===1);await resolve(0);await waitUpdated();
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await page.waitForFunction(()=>JSON.parse(localStorage.getItem('omaax.figure.alice')).document.items[0].svg.includes('Original chart'));
  assert.deepEqual((await saved()).items.slice(1),seed.items.slice(1));
  await page.getByRole('button',{name:'重做',exact:true}).click();await waitUpdated();
});
test('locked and hidden layers cannot be submitted for model editing',async()=>{
  for(const name of ['Locked panel','Hidden panel']){
    await generate(name);await page.getByRole('alert').waitFor();
    assert.match(await page.getByRole('alert').textContent(),/未锁定的可见图层/);
    assert.equal((await modelCalls()).length,0);
  }
  assert.deepEqual((await saved()).items.slice(1),seed.items.slice(1));
});
test('selected raster bytes survive a model response that adds vector annotations',async()=>{
  await generate('Original image');await page.waitForFunction(()=>window.__qa.pending.length===1);
  const input=(await modelCalls())[0].body.svg;assert.ok(input.includes(image));
  const annotated=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 60"><image width="80" height="60" href="${image}"/><text x="2" y="12">A</text></svg>`;
  await resolve(0,annotated);await page.waitForFunction(()=>JSON.parse(localStorage.getItem('omaax.figure.alice')).document.items.find(i=>i.id==='raster').kind==='svg');
  const result=await saved(),raster=result.items.find(i=>i.id==='raster');
  assert.ok(raster.svg.includes(image));assert.match(raster.svg,/>A<\/text>/);
  for(const key of ['id','name','x','y','width','height','rotation'])assert.equal(raster[key],seed.items[3][key],key);
  assert.deepEqual(result.items.slice(1,3),seed.items.slice(1,3));assert.match(result.items[0].svg,/Original chart/);
});
test('a late stop error cannot overwrite the status of a newer model request',async()=>{
  await page.evaluate(()=>{window.__qa.deferCancel=true;});
  await generate();await page.waitForFunction(()=>window.__qa.pending.length===1);
  await page.getByRole('button',{name:'停止',exact:true}).click();await page.waitForFunction(()=>window.__qa.cancellations.length===1);
  await generate();await page.waitForFunction(()=>window.__qa.pending.length===2);
  await page.evaluate(async()=>{window.__qa.cancellations[0].reject(Error('Old cancellation failed'));await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));});
  assert.equal(await page.getByRole('alert').count(),0);assert.equal(await page.getByRole('button',{name:'停止',exact:true}).count(),1);
  await resolve(0);await resolve(1);await waitUpdated();assert.equal(await page.getByRole('alert').count(),0);
});
