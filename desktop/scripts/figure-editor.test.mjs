import {after, afterEach, before, beforeEach, test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
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
  const entry=`import React from 'react';import{createRoot}from'react-dom/client';import FigureEditor from './src/renderer/src/views/FigureEditor';import{LangContext}from'./src/renderer/src/i18n';import{loadFigureDraft}from'./src/renderer/src/views/figure-draft';
    window.__qa={owner:'alice',pending:[],cancellations:[],calls:[],deferCancel:false};
    window.__qa.loadDraft=async owner=>{const key='omaax.figure.'+owner;return JSON.parse(localStorage.getItem(key)??await loadFigureDraft(key))};
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
  const executablePath=['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(value=>fs.existsSync(value));
  assert.ok(executablePath,'An installed Edge executable is required; tests do not download browsers.');
  browser=await playwright.chromium.launch({headless:true,executablePath});
});
beforeEach(async()=>{
  errors=[];requests=[];
  page=await browser.newPage({viewport:{width:1500,height:1000}});
  page.setDefaultTimeout(5000);
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

const saved=(owner='alice')=>page.evaluate(async id=>(await window.__qa.loadDraft(id)).document,owner);
async function waitForAsync(predicate,argument){
  const deadline=Date.now()+5000;let lastError;
  do{try{if(await page.evaluate(predicate,argument))return;}catch(error){lastError=error;}await page.waitForTimeout(50);}while(Date.now()<deadline);
  assert.fail(lastError?String(lastError):'Timed out waiting for the saved draft or native mock.');
}
const modelCalls=()=>page.evaluate(()=>window.__qa.calls.filter(c=>c.action==='figure_generate'));
async function generate(name='Editable panel'){
  await page.getByRole('button',{name,exact:true}).click();
  const conversation=page.locator('details').filter({has:page.locator('summary').filter({hasText:/^对话修改$/})});
  if(!await conversation.evaluate(el=>el.open))await conversation.locator('summary').click();
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
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items[0].svg.includes('Updated chart'));
}

test('stopping ignores a late model response and retains the current drawing',async()=>{
  await generate();await waitForAsync(async()=>window.__qa.pending.length===1);
  await page.getByRole('button',{name:'停止',exact:true}).click();await resolve(0);
  assert.match((await saved()).items[0].svg,/Original chart/);
  assert.equal(await page.locator('.drawing-notice[role="status"]').textContent(),'已停止，保留修改前的图。');
  const cancel=await page.evaluate(()=>window.__qa.calls.filter(c=>c.action==='figure_cancel'));
  assert.equal(cancel.length,1);assert.equal(cancel[0].body.requestId,(await modelCalls())[0].body.requestId);
});
test('switching accounts cancels the previous request and rejects its late result',async()=>{
  await generate();await waitForAsync(async()=>window.__qa.pending.length===1);
  await page.evaluate(()=>window.__qa.setOwner('bob'));
  await page.getByText('把图表放到这里，开始拼图',{exact:true}).waitFor();await resolve(0);
  await waitForAsync(async()=>(await window.__qa.loadDraft('bob'))!==null);
  assert.equal((await saved('bob')).items.length,0);assert.match((await saved()).items[0].svg,/Original chart/);
  assert.equal(await page.evaluate(()=>window.__qa.calls.filter(c=>c.action==='figure_cancel').length),1);
});
test('model refinement preserves other layers and the selected panel geometry and grouping',async()=>{
  await generate();await waitForAsync(async()=>window.__qa.pending.length===1);await resolve(0);await waitUpdated();
  const result=await saved();assert.equal(result.items.length,seed.items.length);
  for(const key of ['id','name','x','y','width','height','rotation','groupId'])assert.equal(result.items[0][key],seed.items[0][key],key);
  assert.deepEqual(result.items.slice(1),seed.items.slice(1));
  const input=(await modelCalls())[0].body.svg;
  assert.match(input,/Original chart/);assert.doesNotMatch(input,/Protected annotation|data:image\/png/);
});
test('model refinement supports undo and redo without altering the original panels',async()=>{
  await generate();await waitForAsync(async()=>window.__qa.pending.length===1);await resolve(0);await waitUpdated();
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items[0].svg.includes('Original chart'));
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
  await generate('Original image');await waitForAsync(async()=>window.__qa.pending.length===1);
  const input=(await modelCalls())[0].body.svg;assert.ok(input.includes(image));
  const annotated=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 60"><image width="80" height="60" href="${image}"/><text x="2" y="12">A</text></svg>`;
  await resolve(0,annotated);await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.find(i=>i.id==='raster').kind==='svg');
  const result=await saved(),raster=result.items.find(i=>i.id==='raster');
  assert.ok(raster.svg.includes(image));assert.match(raster.svg,/>A<\/text>/);
  for(const key of ['id','name','x','y','width','height','rotation'])assert.equal(raster[key],seed.items[3][key],key);
  assert.deepEqual(result.items.slice(1,3),seed.items.slice(1,3));assert.match(result.items[0].svg,/Original chart/);
});
test('a late stop error cannot overwrite the status of a newer model request',async()=>{
  await page.evaluate(()=>{window.__qa.deferCancel=true;});
  await generate();await waitForAsync(async()=>window.__qa.pending.length===1);
  await page.getByRole('button',{name:'停止',exact:true}).click();await waitForAsync(async()=>window.__qa.cancellations.length===1);
  await generate();await waitForAsync(async()=>window.__qa.pending.length===2);
  await page.evaluate(async()=>{window.__qa.cancellations[0].reject(Error('Old cancellation failed'));await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));});
  assert.equal(await page.getByRole('alert').count(),0);assert.equal(await page.getByRole('button',{name:'停止',exact:true}).count(),1);
  await resolve(0);await resolve(1);await waitUpdated();assert.equal(await page.getByRole('alert').count(),0);
});

test('new text is immediately editable and keyboard deletion inside the field does not delete the layer',async()=>{
  await page.getByRole('button',{name:'添加文字',exact:true}).click();
  const input=page.getByRole('textbox',{name:'文字内容',exact:true});
  assert.equal(await input.evaluate(el=>el===document.activeElement),true);
  await input.fill('Rice SPL family\n研究标签');
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.at(-1).text==='Rice SPL family\n研究标签');
  assert.match(await page.locator('.figure-canvas').textContent(),/Rice SPL family研究标签/);
  await input.press('Control+a');await input.press('Backspace');
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.at(-1).text==='');
  assert.equal((await saved()).items.length,seed.items.length+1);
  await input.fill('Restored text');
  await page.locator('.figure-stage').focus();
  await page.locator('.figure-canvas [data-figure-id]').last().locator('text').dblclick();
  assert.equal(await input.evaluate(el=>el===document.activeElement),true,'double-click returns focus despite canvas pointer capture');
  await page.getByRole('button',{name:'删除所选元素',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.length===4);
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.at(-1).text==='Restored text');
});

test('A to Z labels can be added without selecting a chart and preserve custom labels on export and save',async()=>{
  assert.equal(await page.getByRole('button',{name:'添加 A/B 标签',exact:true}).count(),0);
  assert.deepEqual(await page.getByLabel('标签字母',{exact:true}).locator('option').allTextContents(),Array.from({length:26},(_,i)=>String.fromCharCode(65+i)));
  await page.getByLabel('标签字母',{exact:true}).selectOption('Z');
  await page.getByRole('button',{name:'添加标签',exact:true}).click();
  const input=page.getByRole('textbox',{name:'标签内容',exact:true});
  assert.equal(await input.evaluate(el=>el===document.activeElement),true);
  assert.equal(await input.inputValue(),'Z');
  await input.fill('Fig. 2B');
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.at(-1).text==='Fig. 2B');
  const label=(await saved()).items.at(-1);assert.equal(label.labelFor,undefined);assert.match(label.id,/^label-/);
  await page.locator('.figure-stage').focus();await page.locator('.figure-stage').press('ArrowRight');
  await waitForAsync(async id=>(await window.__qa.loadDraft('alice')).document.items.find(i=>i.id===id).x===121,label.id);
  await page.getByLabel('标签字母',{exact:true}).selectOption('A');
  await page.getByRole('button',{name:'添加标签',exact:true}).click();
  assert.equal(await input.inputValue(),'A');
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.length===6);
  assert.equal((await saved()).items.find(i=>i.id===label.id).text,'Fig. 2B');
  await page.getByRole('button',{name:'删除所选元素',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.length===5);
  await page.getByRole('button',{name:'导出 SVG',exact:true}).click();
  const exported=await page.evaluate(()=>window.__qa.calls.findLast(c=>c.action==='figure_export').body.svg);
  assert.match(exported,/Fig\. 2B/);assert.ok(exported.includes(image));
  await page.evaluate(()=>{window.biodsh.pickFolder=async()=> 'C:/isolated/project';});
  await page.getByRole('button',{name:'保存到项目',exact:true}).click();
  const project=await page.evaluate(()=>window.__qa.calls.findLast(c=>c.action==='figure_save').body);
  assert.equal(JSON.parse(project.document).items.at(-1).text,'Fig. 2B');assert.match(project.svg,/Fig\. 2B/);
});

test('labels work on an empty canvas and preserve legacy attached labels when a saved project opens',async()=>{
  await page.evaluate(()=>window.__qa.setOwner('empty'));
  await page.getByText('把图表放到这里，开始拼图',{exact:true}).waitFor();
  await page.getByLabel('标签字母',{exact:true}).selectOption('Q');
  await page.getByRole('button',{name:'添加标签',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('empty'))?.document.items[0]?.text==='Q');
  await page.evaluate(seed=>{
    localStorage.setItem('omaax.figure.legacy',JSON.stringify({document:{...seed,items:[...seed.items,{id:'old-label',name:'Publication label',kind:'text',text:'Fig. 3α',labelFor:'raster',x:330,y:220,width:100,height:35,rotation:0,hidden:false,locked:false,fontSize:24,color:'#123456'}]},history:[]}));
    window.__qa.setOwner('legacy');
  },seed);
  await page.getByRole('button',{name:'Publication label',exact:true}).click();
  assert.equal(await page.getByLabel('标签内容',{exact:true}).inputValue(),'Fig. 3α');
  await page.getByRole('button',{name:'添加标签',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('legacy')).document.items.length===6);
  const legacy=(await saved('legacy')).items.find(i=>i.id==='old-label');
  assert.equal(legacy.text,'Fig. 3α');assert.equal(legacy.labelFor,'raster');assert.equal(legacy.x,330);
});

test('editing long multiline text expands its box and fitting keeps every displayed glyph inside the canvas',async()=>{
  await page.getByRole('button',{name:'添加文字',exact:true}).click();
  await page.getByLabel('文字内容',{exact:true}).fill('A very long research annotation with a descender: gjpq\n第二行研究标签\nThird line');
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.at(-1).height>100);
  const text=(await saved()).items.at(-1);assert.ok(text.width>180);assert.ok(text.height>48);
  await page.getByRole('button',{name:'画布适应内容',exact:true}).click();
  const bounds=await page.locator('.figure-canvas').evaluate(el=>{
    const svg=el.querySelector('svg'),text=svg.querySelectorAll('[data-figure-id] text'),viewport=svg.getBoundingClientRect();
    return [...text].map(node=>{const rect=node.getBoundingClientRect();return {left:rect.left-viewport.left,top:rect.top-viewport.top,right:viewport.right-rect.right,bottom:viewport.bottom-rect.bottom};});
  });
  assert.ok(bounds.every(box=>Object.values(box).every(value=>value>=0)),JSON.stringify(bounds));
});

test('oversized text changes are rejected before they can corrupt the saved draft',async()=>{
  await page.getByRole('button',{name:'添加文字',exact:true}).click();
  const input=page.getByLabel('文字内容',{exact:true});await input.fill('Keep this text');
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.at(-1).text==='Keep this text');
  const before=await saved();await input.fill('W'.repeat(4000));
  assert.match(await page.getByRole('alert').textContent(),/文字内容过长/);
  assert.equal(await input.inputValue(),'Keep this text');assert.deepEqual(await saved(),before);
});

test('new rectangles have no fill, existing fill can be removed, and selected layers delete with undo',async()=>{
  await page.getByRole('button',{name:'添加矩形',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.length===5);
  assert.equal((await saved()).items.at(-1).fill,'none');
  assert.equal(await page.getByLabel('无填充',{exact:true}).isChecked(),true);
  assert.equal(await page.getByLabel('填充颜色',{exact:true}).isDisabled(),true);
  const id=(await saved()).items.at(-1).id;
  assert.equal(await page.locator(`[data-figure-id="${id}"] rect`).nth(1).getAttribute('fill'),'none');
  await page.getByLabel('无填充',{exact:true}).uncheck();
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.at(-1).fill!=='none');
  await page.getByLabel('无填充',{exact:true}).check();
  await page.getByRole('button',{name:'删除所选元素',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.length===4);
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await waitForAsync(async()=>(await window.__qa.loadDraft('alice')).document.items.length===5);
  assert.equal((await saved()).items.at(-1).fill,'none');
  await page.getByRole('button',{name:'Locked panel',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'删除所选元素',exact:true}).isDisabled(),true);
  for(const name of ['Original image','Hidden panel','Editable panel']){
    await page.getByRole('button',{name,exact:true}).click();
    await page.getByRole('button',{name:'删除所选元素',exact:true}).click();
    await waitForAsync(async name=>!(await window.__qa.loadDraft('alice')).document.items.some(i=>i.name===name),name);
  }
  assert.equal((await saved()).items.find(i=>i.id==='locked').text,'Protected annotation');
});
