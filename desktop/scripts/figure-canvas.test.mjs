import {after,afterEach,before,beforeEach,test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {build} from 'esbuild';

const root=fileURLToPath(new URL('../',import.meta.url)),require=createRequire(import.meta.url);
let playwright;try{playwright=require('playwright');}catch{playwright=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json'))('playwright');}
const edge=['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(value=>fs.existsSync(value));
const item=(id,patch={})=>({id,name:id,kind:'rect',x:120,y:120,width:120,height:80,rotation:0,hidden:false,locked:false,fill:'#639ad7',stroke:'#24486a',...patch});
const documentFor=items=>({format:'omaax-figure',version:1,title:'Canvas interaction',width:900,height:650,widthMm:180,items});
const nestedSvg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="100 50 200 150"><g data-vector-id="group" transform="translate(115 65) rotate(17) scale(1.15 .85)"><rect data-vector-id="rect" width="35" height="20" transform="rotate(-9 17.5 10)" fill="#4377aa"/><text data-vector-id="label" x="0" y="45" font-size="12"><tspan data-vector-id="span">Label</tspan></text></g><svg data-vector-id="nested" x="210" y="65" width="55" height="45" viewBox="0 0 110 90"><rect data-vector-id="nested-rect" x="8" y="9" width="45" height="30" fill="#a75373"/></svg><rect data-vector-id="solo" x="235" y="140" width="28" height="25" fill="#297a55"/></svg>';
const panel=item('panel',{kind:'svg',x:140,y:100,width:360,height:270,rotation:23,svg:nestedSvg});
const css=`*{box-sizing:border-box}body{margin:0;background:#eee}.figure-stage{position:relative;width:1100px;height:760px;overflow:auto;padding:40px;touch-action:none;user-select:none}.figure-canvas{position:relative;flex:none}.figure-artwork,.figure-controls{position:absolute;inset:0;width:100%;height:100%}.figure-artwork>svg{display:block;width:100%;height:100%}.figure-controls{pointer-events:none;overflow:visible}.figure-selection-box{fill:none;stroke:#1688f8;stroke-width:1;pointer-events:none}.figure-handle{pointer-events:all;fill:white;stroke:#1688f8;stroke-width:1}.figure-marquee{fill:#1688f822;stroke:#1688f8;pointer-events:none}`;
let browser,page,bundle,errors,requests;
before(async()=>{
  assert.ok(edge,'An installed Edge executable is required; this test never downloads a browser.');
  const entry=`import React,{useLayoutEffect,useRef,useState}from'react';import{createRoot}from'react-dom/client';import FigureCanvas from'./src/renderer/src/views/FigureCanvas';import{parseFigure}from'./src/renderer/src/views/figure-model';import{LangContext}from'./src/renderer/src/i18n';
    window.__qa={commits:0,ends:0,text:[],errors:[]};
    function App(){const[doc,setDoc]=useState(${JSON.stringify(documentFor([panel]))}),[selected,setSelected]=useState(['panel']),[inside,setInside]=useState('panel'),[nodes,setNodes]=useState(['rect']),[zoom,setZoom]=useState(.75),[busy,setBusy]=useState(false),[tool,setTool]=useState('select'),ref=useRef(null);
      const change=next=>{setDoc(next);window.__qa.commits++};
      useLayoutEffect(()=>{window.__qa.state={doc,selected,inside,nodes,zoom,busy,tool};window.__qa.roundtrip=()=>parseFigure(JSON.stringify(doc));window.__qa.configure=value=>{if(value.doc)setDoc(value.doc);if(value.selected)setSelected(value.selected);if('inside'in value)setInside(value.inside);if(value.nodes)setNodes(value.nodes);if('zoom'in value)setZoom(value.zoom);if('busy'in value)setBusy(value.busy);if(value.tool)setTool(value.tool)};window.__qa.nudge=(x,y)=>ref.current.nudge(x,y);window.__qa.align=mode=>ref.current.align(mode)});
      return <FigureCanvas ref={ref} doc={doc} selected={selected} inside={inside} nodes={nodes} zoom={zoom} busy={busy} tool={tool} onSelect={setSelected} onNodes={setNodes} onEnter={setInside} onChange={setDoc} onCommit={change} onEnd={original=>{window.__qa.ends++;window.__qa.original=original}} onText={vector=>window.__qa.text.push(vector)} onError={error=>window.__qa.errors.push(String(error))} onFiles={()=>{}} onKey={event=>{const vector={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];if(vector){event.preventDefault();ref.current.nudge(vector[0]*(event.shiftKey?10:1),vector[1]*(event.shiftKey?10:1))}}}/>}
    createRoot(document.getElementById('root')).render(<LangContext.Provider value="zh"><App/></LangContext.Provider>);`;
  const output=await build({stdin:{contents:entry,resolveDir:root,loader:'tsx'},write:false,bundle:true,platform:'browser',format:'iife',jsx:'automatic',define:{'import.meta.env.DEV':'false'}});
  bundle=output.outputFiles[0].text;browser=await playwright.chromium.launch({headless:true,executablePath:edge});
});
beforeEach(async()=>{
  errors=[];requests=[];page=await browser.newPage({viewport:{width:1200,height:850}});
  page.on('pageerror',error=>errors.push(String(error)));
  await page.route('**/*',route=>route.request().url()==='http://127.0.0.1/canvas-test'?route.fulfill({contentType:'text/html',body:`<!DOCTYPE html><html><head><style>${css}</style></head><body><div id="root"></div></body></html>`}):(requests.push(route.request().url()),route.abort()));
  await page.goto('http://127.0.0.1/canvas-test');await page.addScriptTag({content:bundle});await page.waitForFunction(()=>window.__qa.state);
});
afterEach(async()=>{const caught=await page.evaluate(()=>window.__qa.errors);await page.close();assert.deepEqual(caught,[]);assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);});
after(async()=>{await browser?.close();});
const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
async function configure(value){await page.evaluate(value=>window.__qa.configure(value),value);await settle();}
const state=()=>page.evaluate(()=>window.__qa.state);
const vector=id=>`.figure-artwork [data-figure-id="panel"] [data-vector-id="${id}"]`;
async function corners(selector){return page.locator(selector).evaluate(el=>{const root=document.querySelector('.figure-artwork>svg'),box=el.getBBox(),m=root.getScreenCTM().inverse().multiply(el.getScreenCTM());return [[box.x,box.y],[box.x+box.width,box.y],[box.x+box.width,box.y+box.height],[box.x,box.y+box.height]].map(([x,y])=>{const p=new DOMPoint(x,y).matrixTransform(m);return{x:p.x,y:p.y}})});}
const bounds=points=>{const x=Math.min(...points.map(p=>p.x)),y=Math.min(...points.map(p=>p.y));return{x,y,width:Math.max(...points.map(p=>p.x))-x,height:Math.max(...points.map(p=>p.y))-y}};
function near(actual,expected,tolerance=.03){assert.ok(Math.abs(actual-expected)<tolerance,`${actual} differs from ${expected}`);}
function pointsNear(actual,expected,tolerance=.03){actual.forEach((point,i)=>{near(point.x,expected[i].x,tolerance);near(point.y,expected[i].y,tolerance)});}
async function screenPoint(x,y){return page.locator('.figure-artwork>svg').evaluate((el,{x,y})=>{const p=new DOMPoint(x,y).matrixTransform(el.getScreenCTM());return{x:p.x,y:p.y}},{x,y});}
async function drag(from,to,modifiers=[]){for(const key of modifiers)await page.keyboard.down(key);await page.mouse.move(from.x,from.y);await page.mouse.down();await page.mouse.move(to.x,to.y,{steps:6});await page.mouse.up();for(const key of modifiers.reverse())await page.keyboard.up(key);await settle();}
async function center(selector){const box=await page.locator(selector).boundingBox();assert.ok(box);return{x:box.x+box.width/2,y:box.y+box.height/2};}

test('nested viewport and transformed parent use canvas-space nudge without cumulative drift',async()=>{
  await configure({nodes:['nested-rect'],zoom:.65});const before=await corners(vector('nested-rect'));
  await page.locator('.figure-stage').focus();await page.keyboard.press('ArrowRight');await page.keyboard.press('Shift+ArrowDown');await settle();
  pointsNear(await corners(vector('nested-rect')),before.map(p=>({x:p.x+1,y:p.y+10})),.00001);
  await page.keyboard.press('ArrowLeft');await page.keyboard.press('Shift+ArrowUp');await settle();pointsNear(await corners(vector('nested-rect')),before,.00001);
});

test('dragging a nested rotated element follows the pointer at non-unit zoom and returns to its origin',async()=>{
  const before=await corners(vector('rect')),start=await center(vector('rect'));
  await drag(start,{x:start.x+30,y:start.y+15});pointsNear(await corners(vector('rect')),before.map(p=>({x:p.x+40,y:p.y+20})));
  assert.equal(await page.evaluate(()=>window.__qa.ends),1);
  const moved=await center(vector('rect'));await page.mouse.move(moved.x,moved.y);await page.mouse.down();await page.mouse.move(moved.x+20,moved.y+10);await page.mouse.move(moved.x,moved.y);await page.mouse.up();await settle();
  pointsNear(await corners(vector('rect')),before.map(p=>({x:p.x+40,y:p.y+20})));
});

test('Shift corner scaling retains the exact affine transform of a nested vector',async()=>{
  const before=await corners(vector('rect')),box=bounds(before),start=await center('[aria-label="缩放控制点 se"]');
  const target=await screenPoint(box.x+box.width*1.35,box.y+box.height*.7);await drag(start,target,['Shift']);
  pointsNear(await corners(vector('rect')),before.map(p=>({x:box.x+(p.x-box.x)*1.35,y:box.y+(p.y-box.y)*.7})),.08);
});

test('rotation applies one world-space transform across a transformed parent',async()=>{
  const before=await corners(vector('rect')),box=bounds(before),cx=box.x+box.width/2,cy=box.y+box.height/2,start=await center('[aria-label="旋转控制点"]');
  const target=await screenPoint(cx+(box.height/2+26/.75),cy);await drag(start,target);
  pointsNear(await corners(vector('rect')),before.map(p=>({x:cx-(p.y-cy),y:cy+(p.x-cx)})),.08);
});

test('parent-child selection is normalized and alignment does not transform a child twice',async()=>{
  await configure({nodes:['group','rect','solo']});assert.deepEqual((await state()).nodes,['group','solo']);
  const groupBefore=await corners(vector('group')),rectBefore=await corners(vector('rect')),soloBefore=await corners(vector('solo'));
  const left=Math.min(bounds(groupBefore).x,bounds(soloBefore).x);await page.evaluate(()=>window.__qa.align('left'));await settle();
  near(bounds(await corners(vector('group'))).x,left);near(bounds(await corners(vector('solo'))).x,left);
  pointsNear(await corners(vector('rect')),rectBefore.map(p=>({x:p.x+left-bounds(groupBefore).x,y:p.y})));
});

test('marquee uses its final pointer position and removes selected text descendants',async()=>{
  await configure({nodes:[]});await drag(await screenPoint(40,30),await screenPoint(700,540));
  const nodes=(await state()).nodes;assert.ok(nodes.includes('rect'));assert.ok(nodes.includes('label'));assert.ok(nodes.includes('nested-rect'));assert.ok(!nodes.includes('span'));assert.ok(!nodes.includes('group'));
});

test('locked descendants block parent gestures, nudge and alignment without producing errors',async()=>{
  const locked={...panel,svg:nestedSvg.replace('data-vector-id="rect"','data-vector-id="rect" data-vector-locked="true"')};
  await configure({doc:documentFor([locked]),nodes:['group']});const before=(await state()).doc;
  assert.equal(await page.locator('.figure-handle').count(),0);await page.evaluate(()=>window.__qa.nudge(10,12));await page.evaluate(()=>window.__qa.align('left'));await settle();
  assert.deepEqual((await state()).doc,before);assert.equal(await page.evaluate(()=>window.__qa.commits),0);
  const at=await center(vector('rect'));await drag(at,{x:at.x+30,y:at.y+15});assert.deepEqual((await state()).doc,before);
});

test('oblique outer items scale proportionally under Shift while quarter turns support axis scaling',async()=>{
  const rotated=item('outer',{rotation:37});await configure({doc:documentFor([rotated]),inside:null,nodes:[],selected:['outer']});
  let before=await corners('[data-figure-id="outer"]'),box=bounds(before),start=await center('[aria-label="缩放控制点 se"]');
  await drag(start,await screenPoint(box.x+box.width*1.4,box.y+box.height*.8),['Shift']);
  let changed=(await state()).doc.items[0];near(changed.width/changed.height,rotated.width/rotated.height,.000001);near(changed.rotation,37,.000001);
  const scale=changed.width/rotated.width;pointsNear(await corners('[data-figure-id="outer"]'),before.map(p=>({x:box.x+(p.x-box.x)*scale,y:box.y+(p.y-box.y)*scale})),.08);
  await configure({doc:documentFor([item('outer',{rotation:90})])});before=await corners('[data-figure-id="outer"]');box=bounds(before);start=await center('[aria-label="缩放控制点 se"]');
  await drag(start,await screenPoint(box.x+box.width*1.3,box.y+box.height*.7),['Shift']);pointsNear(await corners('[data-figure-id="outer"]'),before.map(p=>({x:box.x+(p.x-box.x)*1.3,y:box.y+(p.y-box.y)*.7})),.08);
});

test('Alt cycles overlapping panels and a group with a locked member is immovable',async()=>{
  const first=item('bottom'),second=item('top',{x:150,y:145,fill:'#b96772'});await configure({doc:documentFor([first,second]),inside:null,nodes:[],selected:['top']});
  const at=await screenPoint(180,170);await page.keyboard.down('Alt');await page.mouse.click(at.x,at.y);await page.keyboard.up('Alt');await settle();assert.deepEqual((await state()).selected,['bottom']);
  await page.keyboard.down('Alt');await page.mouse.click(at.x,at.y);await page.keyboard.up('Alt');await settle();assert.deepEqual((await state()).selected,['top']);
  await configure({doc:documentFor([{...first,groupId:'group'},{...second,groupId:'group',locked:true}]),selected:['bottom','top']});
  const before=(await state()).doc;await page.evaluate(()=>window.__qa.nudge(10,0));await settle();assert.deepEqual((await state()).doc,before);assert.equal(await page.locator('.figure-handle').count(),0);
});

test('double-click picks whole vector text and reports the correct editor without stale inside state',async()=>{
  await configure({inside:null,nodes:[],selected:[]});await page.locator(vector('span')).dblclick();await settle();
  assert.equal((await state()).inside,'panel');assert.deepEqual((await state()).nodes,['label']);assert.deepEqual(await page.evaluate(()=>window.__qa.text),[true]);
  const text=item('text',{kind:'text',x:520,y:180,width:200,height:50,text:'Annotation',fontSize:25});await configure({doc:documentFor([panel,text])});
  await page.locator('[data-figure-id="text"] text').dblclick();await settle();assert.equal((await state()).inside,null);assert.deepEqual(await page.evaluate(()=>window.__qa.text),[true,false]);
});

test('pointer cancellation rolls a preview back and busy canvas rejects edits',async()=>{
  const before=(await state()).doc,start=await center(vector('rect'));await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(start.x+30,start.y+20);await page.locator('.figure-stage').dispatchEvent('pointercancel');await page.mouse.up();await settle();
  assert.deepEqual((await state()).doc,before);assert.equal(await page.evaluate(()=>window.__qa.ends),0);
  await configure({busy:true});await page.evaluate(()=>window.__qa.nudge(10,10));await page.locator(vector('span')).dblclick();await settle();assert.deepEqual((await state()).doc,before);assert.deepEqual(await page.evaluate(()=>window.__qa.text),[]);
});

test('multi-selection moves once across different vector parents and never touches another panel with matching node ids',async()=>{
  const copy={...panel,id:'copy',x:510,y:350,width:260,height:195,rotation:0};await configure({doc:documentFor([panel,copy]),nodes:['rect','nested-rect']});
  const first=await corners(vector('rect')),second=await corners(vector('nested-rect')),copyBefore=await corners('[data-figure-id="copy"] [data-vector-id="rect"]'),start=await center(vector('rect'));
  await drag(start,{x:start.x+15,y:start.y+9});
  pointsNear(await corners(vector('rect')),first.map(p=>({x:p.x+20,y:p.y+12})));pointsNear(await corners(vector('nested-rect')),second.map(p=>({x:p.x+20,y:p.y+12})));
  pointsNear(await corners('[data-figure-id="copy"] [data-vector-id="rect"]'),copyBefore,.000001);
});

test('outer text scaling changes the text size with its selection box',async()=>{
  const text=item('text',{kind:'text',x:170,y:150,width:200,height:50,text:'Annotation',fontSize:25});await configure({doc:documentFor([text]),inside:null,nodes:[],selected:['text']});
  const before=await corners('[data-figure-id="text"]'),box=bounds(before),start=await center('[aria-label="缩放控制点 se"]');
  await drag(start,await screenPoint(box.x+box.width*1.5,box.y+box.height*1.5));
  const changed=(await state()).doc.items[0];near(changed.fontSize,37.5,.02);near(changed.width,300,.05);near(changed.height,75,.05);
});

test('shrinking native text clamps its font and preserves a reopenable document',async()=>{
  const text=item('text',{kind:'text',x:170,y:150,width:200,height:50,text:'Small annotation',fontSize:32});await configure({doc:documentFor([text]),inside:null,nodes:[],selected:['text']});
  const before=await corners('[data-figure-id="text"]'),box=bounds(before),start=await center('[aria-label="缩放控制点 se"]');
  await drag(start,await screenPoint(box.x+box.width*.005,box.y+box.height*.005));
  const changed=(await state()).doc.items[0];near(changed.fontSize,1,.000001);assert.ok(changed.width>=.01&&changed.height>=.01);near(changed.width/changed.height,text.width/text.height,.000001);
  const restored=await page.evaluate(()=>window.__qa.roundtrip());assert.equal(restored.items[0].fontSize,1);await configure({doc:restored});assert.equal((await state()).doc.items[0].fontSize,1);assert.equal(await page.locator('[data-figure-id="text"] text').textContent(),'Small annotation');
});
