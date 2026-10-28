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
const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5E3yQAAAAASUVORK5CYII=';
const source='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 180"><rect data-vector-id="first" x="15" y="20" width="60" height="45" fill="#cc3344"><title>First same color</title></rect><rect data-vector-id="second" x="105" y="20" width="60" height="45" fill="#cc3344"><title>Second same color</title></rect><circle data-vector-id="third" cx="220" cy="42" r="23" fill="#cc3344"><title>Third same color</title></circle><text data-vector-id="text-one" x="15" y="115" font-family="Arial" font-size="18"><title>Main label</title><tspan data-vector-id="text-span">Original caption</tspan></text><text data-vector-id="text-two" x="15" y="150" font-family="Arial" font-size="15"><title>Other label</title>Untouched caption</text></svg>';
const seed={format:'omaax-figure',version:1,title:'Publication integration',width:700,height:500,widthMm:100,items:[
  {id:'panel',name:'Source SVG',kind:'svg',x:40,y:45,width:280,height:180,rotation:0,hidden:false,locked:false,svg:source},
  {id:'raster',name:'Original raster',kind:'image',x:390,y:70,width:90,height:90,rotation:0,hidden:false,locked:false,dataUrl:png},
]};
const baseCss=':root{--bg:#f5f6f9;--surface:#fff;--text:#202735;--text-2:#687180;--border:#dce0e7;--accent:#2878e8;--accent-soft:#e8f1ff}*{box-sizing:border-box}html,body,#root{height:100%;margin:0}body{font-family:Arial,sans-serif}button,input,select,textarea{font:inherit}button{cursor:pointer;border:1px solid var(--border);border-radius:6px;background:#fff;color:var(--text)}button:disabled{cursor:default}.btn{padding:6px 10px}.btn-primary{background:var(--accent);color:#fff}';
let browser,page,js,css,errors,requests;
before(async()=>{
  assert.ok(edge,'An installed Edge executable is required. Tests never download browsers.');
  const entry=`import React from'react';import{createRoot}from'react-dom/client';import FigureEditor from'./src/renderer/src/views/FigureEditor';import{LangContext}from'./src/renderer/src/i18n';import{loadFigureDraft}from'./src/renderer/src/views/figure-draft';
    window.__qa={calls:[],saved:null,owner:'integration',pdfTokens:{}};
    window.__qa.loadDraft=async()=>{const key='omaax.figure.integration';return JSON.parse(localStorage.getItem(key)??await loadFigureDraft(key))};
    window.biodsh={dshRpc:async()=>({items:[]}),pickFolder:async()=> 'C:/isolated/figure-project',drawing:async(action,body)=>{
      window.__qa.calls.push({action,body});
      if(action==='figure_fonts')return{families:['Arial','Times New Roman','Calibri']};
      if(action==='figure_save'){window.__qa.saved=body;return{path:'C:/isolated/figure-project/figure.json'}};
      if(action==='figure_open')return window.__qa.saved?{name:'Saved figure',document:window.__qa.saved.document}:null;
      if(action==='figure_export')return{path:'C:/isolated/figure.'+body.format};
      if(action==='figure_pdf_prepare')return{ok:true};
      if(action==='figure_pdf_inspect'){const token='token-'+body.name;window.__qa.pdfTokens[token]=body.name;return{token,name:body.name,totalPages:2,pages:[1,2].map(page=>({page,width:280,height:180,preview:${JSON.stringify(png)}}))}};
      if(action==='figure_pdf_import')return{items:body.pages.map(page=>({name:window.__qa.pdfTokens[body.token]+' page '+page,page,width:280,height:180,warnings:[],...(body.mode==='page'?{dataUrl:${JSON.stringify(png)}}:{svg:window.__qa.invalidPdf?'<svg>':${JSON.stringify(source)}})}))};
      if(action==='figure_generate')throw Error('Unexpected model request: integration tests never call a model.');
      return{ok:true};
    }};
    localStorage.setItem('omaax.figure.integration',window.__seedDraft??JSON.stringify({document:${JSON.stringify(seed)},history:[]}));
    createRoot(document.getElementById('root')).render(<LangContext.Provider value="zh"><FigureEditor active/></LangContext.Provider>);`;
  const output=await build({stdin:{contents:entry,resolveDir:root,loader:'tsx'},write:false,outdir:path.join(root,'test-output'),bundle:true,platform:'browser',format:'iife',jsx:'automatic',define:{'import.meta.env.DEV':'false'},plugins:[{name:'native-account-only',setup(builder){
    builder.onResolve({filter:/\/AuthGate$/},()=>({path:'account',namespace:'native-account-only'}));
    builder.onLoad({filter:/.*/,namespace:'native-account-only'},()=>({contents:"export function useAuth(){return{user:{id:'integration'}}}",loader:'js'}));
  }}]});
  js=output.outputFiles.find(file=>file.path.endsWith('.js')).text;css=output.outputFiles.find(file=>file.path.endsWith('.css')).text;
  browser=await playwright.chromium.launch({headless:true,executablePath:edge});
});
async function openEditor(seedOverride){
  page=await browser.newPage({viewport:{width:1600,height:1100}});page.setDefaultTimeout(6000);
  page.on('pageerror',error=>errors.push(String(error)));page.on('dialog',dialog=>dialog.accept());
  await page.route('**/*',route=>route.request().url()==='http://127.0.0.1/editor-v2-test'?route.fulfill({contentType:'text/html',body:`<!DOCTYPE html><html><head><style>${baseCss}\n${css}</style></head><body><div id="root"></div></body></html>`}):(requests.push(route.request().url()),route.abort()));
  await page.goto('http://127.0.0.1/editor-v2-test');if(seedOverride!==undefined)await page.evaluate(value=>{window.__seedDraft=value},seedOverride);await page.addScriptTag({content:js});
}
beforeEach(async()=>{errors=[];requests=[];await openEditor();await page.getByRole('button',{name:'Source SVG',exact:true}).waitFor();await waitDraft(doc=>doc.items.length===2);});
afterEach(async()=>{const modelCalls=await page.evaluate(()=>window.__qa.calls.filter(call=>call.action==='figure_generate'));await page.close();assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.deepEqual(modelCalls,[]);});
after(async()=>{await browser?.close();});
const saved=()=>page.evaluate(async()=>(await window.__qa.loadDraft())?.document);
async function waitDraft(predicate){const deadline=Date.now()+6000;let value;do{value=await saved();if(value&&predicate(value))return value;await page.waitForTimeout(50);}while(Date.now()<deadline);assert.fail('Saved draft did not reach the expected state: '+JSON.stringify(value)?.slice(0,500));}
async function waitCall(action,count=1){const deadline=Date.now()+6000;do{const calls=await page.evaluate(action=>window.__qa.calls.filter(call=>call.action===action),action);if(calls.length>=count)return calls.at(-1);await page.waitForTimeout(40);}while(Date.now()<deadline);assert.fail('Native call did not arrive: '+action);}
async function enter(){await page.getByRole('button',{name:'Source SVG',exact:true}).click();await page.getByRole('button',{name:'编辑图内元素',exact:true}).click();}
const object=name=>page.locator('.figure-object-name').filter({hasText:name});
const node=id=>page.locator(`.figure-artwork [data-figure-id="panel"] [data-vector-id="${id}"]`);
async function setColor(value){const field=page.getByRole('textbox',{name:'填充 / 文字颜色',exact:true});await field.fill(value);await field.press('Tab');}
async function importFiles(files){await page.locator('input[type=file]').evaluate((input,files)=>{const transfer=new DataTransfer();for(const file of files){const bytes=file.base64?Uint8Array.from(atob(file.base64),c=>c.charCodeAt(0)):file.text;transfer.items.add(new File([bytes],file.name,{type:file.type||'',lastModified:file.lastModified||1}))}input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));},files);}

test('selected SVG color edits leave other objects with the same original color unchanged',async()=>{
  await enter();await object('First same color').click();await setColor('#19a974');
  await waitDraft(doc=>doc.items[0].svg.includes('#19a974'));assert.equal(await node('first').getAttribute('fill'),'#19a974');assert.equal(await node('second').getAttribute('fill'),'#cc3344');assert.equal(await node('third').getAttribute('fill'),'#cc3344');
  await object('Second same color').click();await object('Third same color').click({modifiers:['Shift']});await setColor('#4466aa');
  await waitDraft(doc=>(doc.items[0].svg.match(/#4466aa/g)||[]).length===2);assert.equal(await node('first').getAttribute('fill'),'#19a974');assert.equal(await node('second').getAttribute('fill'),'#4466aa');assert.equal(await node('third').getAttribute('fill'),'#4466aa');
});

test('SVG grouping is reversible and keeps independent source objects and raster bytes',async()=>{
  await enter();await object('First same color').click();await object('Second same color').click({modifiers:['Shift']});await page.getByRole('button',{name:'分组',exact:true}).click();
  const grouped=await waitDraft(doc=>/<g[^>]*data-vector-id/.test(doc.items[0].svg));assert.equal(grouped.items[1].dataUrl,png);
  const parentId=await node('first').evaluate(el=>el.parentElement.getAttribute('data-vector-id'));assert.ok(parentId);assert.equal(await node('second').evaluate(el=>el.parentElement.getAttribute('data-vector-id')),parentId);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await waitDraft(doc=>!/<g[^>]*data-vector-id/.test(doc.items[0].svg));assert.equal(await node('first').evaluate(el=>el.parentElement.localName),'svg');
  await page.getByRole('button',{name:'重做',exact:true}).click();await waitDraft(doc=>doc.items[0].svg.includes(parentId));assert.equal(await node('first').evaluate(el=>el.parentElement.getAttribute('data-vector-id')),parentId);
});

test('vector text content, font and size edit only visible text and retain metadata',async()=>{
  await enter();await object('Main label').click();const input=page.getByLabel('图内文字内容',{exact:true});assert.equal(await input.inputValue(),'Original caption');await input.fill('Revised scientific caption');
  await page.getByLabel('字体',{exact:true}).selectOption('Times New Roman');await page.getByLabel('印刷字号（pt）',{exact:true}).fill('12');await page.getByRole('button',{name:'粗体',exact:true}).click();
  await waitDraft(doc=>doc.items[0].svg.includes('Revised scientific caption')&&doc.items[0].svg.includes('Times New Roman')&&doc.items[0].svg.includes('font-weight="700"'));
  assert.equal(await node('text-one').getAttribute('font-family'),'Times New Roman');assert.equal(await node('text-span').textContent(),'Revised scientific caption');assert.equal(await node('text-one').locator('title').textContent(),'Main label');assert.match(await node('text-two').textContent(),/Untouched caption/);assert.equal(await node('text-two').getAttribute('font-family'),'Arial');
  const fontSize=Number(await node('text-one').getAttribute('font-size'));assert.ok(Math.abs(fontSize-12*25.4/72*700/100)<.001);
});

test('the whole-panel text editor excludes metadata and updates the visible tspan',async()=>{
  await page.getByRole('button',{name:'Source SVG',exact:true}).click();const input=page.getByLabel('文字 1',{exact:true});assert.equal(await input.inputValue(),'Original caption');
  await input.fill('Panel editor caption');await waitDraft(doc=>doc.items[0].svg.includes('Panel editor caption'));
  assert.equal(await node('text-span').textContent(),'Panel editor caption');assert.equal(await node('text-one').locator('title').textContent(),'Main label');assert.match(await node('text-two').textContent(),/Untouched caption/);
});

test('raster crop updates its viewport while retaining the exact original PNG bytes',async()=>{
  await page.getByRole('button',{name:'Original raster',exact:true}).click();
  for(const [label,value] of [['裁剪左边','10'],['裁剪顶部','20'],['裁剪宽度','70'],['裁剪高度','60']])await page.getByLabel(label,{exact:true}).fill(value);
  const doc=await waitDraft(doc=>doc.items[1].crop?.width===70&&doc.items[1].crop?.height===60);assert.deepEqual(doc.items[1].crop,{x:10,y:20,width:70,height:60});assert.equal(doc.items[1].dataUrl,png);
  assert.equal(await page.locator('.figure-artwork [data-figure-id="raster"] image').getAttribute('href'),png);assert.equal(await page.locator('.figure-artwork [data-figure-id="raster"] svg').getAttribute('viewBox'),'9 18 63 54');
  await page.getByRole('button',{name:'重置裁剪',exact:true}).click();await waitDraft(doc=>doc.items[1].crop===undefined);assert.equal((await saved()).items[1].dataUrl,png);
});

test('PNG with an empty MIME type imports from its real signature without changing bytes',async()=>{
  await importFiles([{name:'empty-mime.png',type:'',base64:png.split(',')[1]}]);await page.getByRole('button',{name:'empty-mime.png',exact:true}).waitFor();
  const doc=await waitDraft(doc=>doc.items.length===3);assert.equal(doc.items[2].kind,'image');assert.equal(doc.items[2].dataUrl,png);assert.equal(doc.items[2].width,1);assert.equal(doc.items[2].height,1);
});

test('SVG and PNG exports contain valid artwork payloads without editor selection controls',async()=>{
  await enter();await object('First same color').click();await page.getByRole('button',{name:'导出 SVG',exact:true}).click();const svg=(await waitCall('figure_export')).body.svg;
  const parsed=await page.evaluate(svg=>{const xml=new DOMParser().parseFromString(svg,'image/svg+xml');return{bad:!!xml.querySelector('parsererror'),root:xml.documentElement.localName,images:[...xml.querySelectorAll('image')].map(image=>image.getAttribute('href')),controls:xml.querySelectorAll('.figure-controls,.figure-handle,.figure-selection-box').length}},svg);
  assert.deepEqual(parsed,{bad:false,root:'svg',images:[png],controls:0});
  await page.getByRole('combobox',{name:/^PNG 分辨率/}).selectOption('150');await page.getByRole('button',{name:'导出 PNG',exact:true}).click();const output=(await waitCall('figure_export',2)).body;
  assert.equal(output.format,'png');assert.match(output.dataUrl,/^data:image\/png;base64,iVBOR/);
  const size=await page.evaluate(async dataUrl=>{const image=new Image();image.src=dataUrl;await image.decode();return{width:image.naturalWidth,height:image.naturalHeight}},output.dataUrl);
  const width=Math.ceil(100/25.4*150);assert.deepEqual(size,{width,height:Math.ceil(width*500/700)});
});

test('saving a project and reopening restores the edited document and original raster',async()=>{
  await page.getByLabel('图标题',{exact:true}).fill('Paper figure final');await page.getByRole('button',{name:'保存到项目',exact:true}).click();const result=(await waitCall('figure_save')).body;
  assert.equal(result.project,'C:/isolated/figure-project');const document=JSON.parse(result.document);assert.equal(document.title,'Paper figure final');assert.equal(document.items[1].dataUrl,png);assert.match(result.svg,/<svg/);
  await page.getByRole('button',{name:'新建',exact:true}).click();await page.getByText('把图表放到这里，开始拼图',{exact:true}).waitFor();await waitDraft(doc=>doc.items.length===0);
  await page.getByRole('button',{name:'打开拼图',exact:true}).click();const reopened=await waitDraft(doc=>doc.title==='Paper figure final'&&doc.items.length===2);assert.deepEqual(reopened,document);assert.equal(await page.getByLabel('图标题',{exact:true}).inputValue(),'Paper figure final');
});

test('successful PDF import advances the queue and closes the dialog after the last file',async()=>{
  await importFiles([{name:'first.pdf',text:'%PDF-1.7\nlocal mock fixture',type:''},{name:'chart.bin',text:'%PDF-1.7\nsecond local mock',type:'application/octet-stream'}]);
  let dialog=page.getByRole('dialog',{name:'导入 PDF',exact:true});await dialog.getByText('first.pdf',{exact:true}).waitFor();await dialog.getByRole('button',{name:'全选',exact:true}).click();await dialog.getByRole('button',{name:'导入 2 页',exact:true}).click();
  await dialog.getByText('chart.bin',{exact:true}).waitFor();await dialog.getByRole('radio',{name:/整页作为图片/}).check();await dialog.getByRole('button',{name:'导入 1 页',exact:true}).click();await dialog.waitFor({state:'detached'});
  const doc=await waitDraft(doc=>doc.items.length===5);assert.deepEqual(doc.items.slice(2).map(item=>item.kind),['svg','svg','image']);assert.deepEqual(doc.items.slice(2).map(item=>item.source.page),[1,2,1]);assert.equal(doc.items[4].dataUrl,png);
  const calls=await page.evaluate(()=>window.__qa.calls.filter(call=>call.action.startsWith('figure_pdf_')));assert.deepEqual(calls.map(call=>call.action),['figure_pdf_prepare','figure_pdf_inspect','figure_pdf_import','figure_pdf_prepare','figure_pdf_inspect','figure_pdf_import']);assert.deepEqual(calls[2].body.pages,[1,2]);assert.equal(calls[2].body.mode,'editable');assert.equal(calls[5].body.mode,'page');assert.equal(calls[4].body.name,'chart.bin');assert.match(calls[4].body.dataUrl,/^data:application\/pdf;base64,/);
});

test('locking the outer SVG blocks keyboard deletion and object-tree hiding inside it',async()=>{
  await enter();await object('First same color').click();
  const row=page.locator('.figure-layers>div').filter({has:page.getByRole('button',{name:'Source SVG',exact:true})});await row.getByRole('button',{name:'锁定图层',exact:true}).click();
  const before=(await waitDraft(doc=>doc.items[0].locked)).items[0].svg;await page.locator('.figure-stage').focus();await page.locator('.figure-stage').press('Delete');
  const hide=page.locator('.figure-object-tree>div').filter({has:object('First same color')}).getByRole('button',{name:'隐藏对象',exact:true});
  if(!await hide.isDisabled())await hide.click();
  await page.waitForTimeout(550);assert.equal((await saved()).items[0].svg,before);assert.equal(await node('first').getAttribute('display'),null);assert.equal(await node('first').count(),1);
});

test('an invalid legacy draft survives load failure until the user explicitly creates a new document',async()=>{
  await page.close();const broken={...seed,format:'unsupported-legacy-format'};await openEditor(JSON.stringify({document:broken,history:[]}));
  await page.getByRole('alert').waitFor();await page.waitForTimeout(650);assert.deepEqual((await page.evaluate(()=>window.__qa.loadDraft())).document,broken);
  await page.getByRole('button',{name:'新建',exact:true}).click();const recovered=await waitDraft(doc=>doc.format==='omaax-figure'&&doc.items.length===0);assert.equal(recovered.version,1);
});

test('invalid imported SVG keeps the PDF dialog open with a visible error and a working retry',async()=>{
  const before=await saved();await page.evaluate(()=>{window.__qa.invalidPdf=true});await importFiles([{name:'retry.pdf',text:'%PDF-1.7\nlocal mock fixture',type:'application/pdf'}]);
  const dialog=page.getByRole('dialog',{name:'导入 PDF',exact:true});await dialog.getByRole('button',{name:'导入 1 页',exact:true}).click();
  const error=dialog.getByRole('alert');await error.waitFor();assert.equal(await error.isVisible(),true);assert.match(await error.textContent(),/SVG/);assert.equal(await dialog.isVisible(),true);
  await page.waitForTimeout(450);assert.deepEqual(await saved(),before);
  await page.evaluate(()=>{window.__qa.invalidPdf=false});await dialog.getByRole('button',{name:'导入 1 页',exact:true}).click();await dialog.waitFor({state:'detached'});await waitDraft(doc=>doc.items.length===3);
});
