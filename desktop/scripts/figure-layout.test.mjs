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
test('expanded figure canvas keeps page controls reachable by pointer and wheel',async()=>{
 const entry=`import React from 'react';import{createRoot}from'react-dom/client';import FigureEditor from './src/renderer/src/views/FigureEditor';import{LangContext}from'./src/renderer/src/i18n';import './src/renderer/src/views/DrawingView.css';
 window.biodsh={drawing:async()=>({families:['Arial']}),dshRpc:async()=>({items:[]})};
 createRoot(document.getElementById('root')).render(<LangContext.Provider value="zh"><main style={{height:'100%'}}><div className="drawing-shell"><nav className="drawing-tabs"><button>论文拼图与精修</button></nav><div className="drawing-tab-content"><FigureEditor active/></div></div></main></LangContext.Provider>);`;
 const built=await build({stdin:{contents:entry,resolveDir:desktop,loader:'tsx'},bundle:true,write:false,outdir:'layout-test',jsx:'automatic',platform:'browser',define:{'import.meta.env.DEV':'false'},plugins:[{name:'test-account',setup(b){b.onResolve({filter:/AuthGate$/},()=>({path:'account',namespace:'mock'}));b.onLoad({filter:/.*/,namespace:'mock'},()=>({contents:'export const useAuth=()=>({user:{id:"layout-test"}});'}));}}]});
 const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
 const browser=await playwright.chromium.launch({channel:'msedge',headless:true});
 const evidence=path.join(desktop,'../validation-local/figure-layout');fs.mkdirSync(evidence,{recursive:true});
 const results=[];
 try{for(const viewport of [{width:1440,height:820},{width:960,height:560},{width:720,height:500}]){
  const page=await browser.newPage({viewport}),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/*',r=>r.fulfill({contentType:'text/html',body:`<!doctype html><html><head><style>*{box-sizing:border-box}html,body,#root{height:100%;margin:0}body{overflow:hidden;font:13px Arial;background:#f5f5f7;color:#222}button{cursor:pointer;padding:6px;border:0;background:white}h1,p{margin:0}:root{--bg:#f5f5f7;--surface:white;--text:#222;--text-2:#666;--border:#ddd;--hairline:#ddd;--accent:#087fff;--accent-soft:#eaf3ff}${css}</style></head><body><div id="root"></div></body></html>`}));
  await page.goto('http://figure-layout.test');await page.addScriptTag({content:js});
  await page.getByRole('button',{name:'添加椭圆',exact:true}).click();
  const outer=page.locator('.figure-editor'),stage=page.getByLabel('拼图画布',{exact:true});
  await outer.evaluate(el=>el.scrollTop=0);await stage.evaluate(el=>el.blur());
  const before=await outer.evaluate(el=>el.scrollTop),box=await stage.boundingBox();
  await page.mouse.click(box.x+Math.min(70,box.width/2),Math.min(viewport.height-20,box.y+80));
  const after=await outer.evaluate(el=>el.scrollTop);
  await page.screenshot({path:path.join(evidence,`${viewport.width}-click.png`)});
  assert.equal(after,before,'Clicking the canvas must not scroll away the page header');
  await outer.evaluate(el=>el.scrollTop=Math.min(220,el.scrollHeight-el.clientHeight));
  const scrolled=await outer.evaluate(el=>el.scrollTop);assert.ok(scrolled>0,'Fixture must have a scrollable page');
  await stage.evaluate(el=>el.scrollTop=0);
  const area=await stage.boundingBox();await page.mouse.move(area.x+Math.min(80,area.width/2),Math.min(viewport.height-30,Math.max(area.y+30,viewport.height/2)));
  await page.mouse.wheel(0,-3000);
  await page.waitForFunction(()=>document.querySelector('.figure-editor').scrollTop===0,{},{timeout:3000});
  assert.ok((await page.getByRole('heading',{name:'论文拼图与精修'}).boundingBox()).y>=0,'Header remains reachable');
  await page.screenshot({path:path.join(evidence,`${viewport.width}-top.png`)});
  results.push({viewport,clickKeptScroll:true,wheelReturnedToHeader:true});assert.deepEqual(errors,[]);await page.close();
 }}finally{await browser.close();fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify(results,null,2));}
});
