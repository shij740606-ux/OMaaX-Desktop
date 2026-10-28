import {test,after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url),React=require('react'),{act,create}=require('react-test-renderer');
const module={exports:{}};
const code=ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/views/FigurePdfImport.tsx',import.meta.url),'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true},
}).outputText;
new Function('require','module','exports',code)(name=>name.endsWith('.css')?{}:name==='../i18n'?{useT:()=>({t:(value,vars={})=>value.replace(/\{(\w+)\}/g,(_,key)=>vars[key]??'')})}:name==='lucide-react'?{X:()=>null}:require(name),module,module.exports);
const FigurePdfImport=module.exports.default;
const globals={FileReader:globalThis.FileReader,document:globalThis.document,HTMLElement:globalThis.HTMLElement};
globalThis.document={activeElement:null};globalThis.HTMLElement=class {};
let readers=[],automaticRead=true;
globalThis.FileReader=class {
  constructor(){readers.push(this);}
  readAsDataURL(file){this.file=file;if(automaticRead)queueMicrotask(()=>this.finish());}
  finish(){if(this.aborted)return;this.result='data:application/pdf;base64,JVBERg==';this.onload?.();this.onloadend?.();}
  abort(){this.aborted=true;this.onabort?.();this.onloadend?.();}
};
after(()=>{for(const [key,value] of Object.entries(globals)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}});
const file={name:'paper.pdf'},png='data:image/png;base64,aGVsbG8=';
const preview=(count=2)=>({token:'private-token',pages:Array.from({length:count},(_,index)=>({page:index+1,width:600,height:800,preview:png}))});
const imported=(page=1)=>({items:[{name:`Page ${page}`,page,width:600,height:800,svg:'<svg viewBox="0 0 600 800"/>',warnings:[]}]});
const text=value=>typeof value==='string'?value:(Array.isArray(value)?value:value?.children||[]).map(text).join(' ');
const settle=async()=>{for(let i=0;i<35;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const button=(tree,label)=>tree.root.findAllByType('button').find(node=>text(node)===label);
const closeButton=tree=>tree.root.findByProps({'aria-label':'关闭 PDF 导入'});
const checkboxes=tree=>tree.root.findAllByType('input').filter(node=>node.props.type==='checkbox');
async function mount(api,extra={}){let tree;await act(async()=>{tree=create(React.createElement(FigurePdfImport,{file,api,onImported:()=>{},onClose:()=>{},...extra}));await settle();});return tree;}
async function click(node){await act(async()=>{node.props.onClick();await settle();});}
async function unmount(tree){await act(async()=>{tree.unmount();await settle();});}

test('prepares before reading, selects pages, and imports the chosen mode without exposing its token',async()=>{
  readers=[];automaticRead=true;
  const preparation=deferred(),calls=[],received=[];
  const api=async(action,body)=>{calls.push({action,body});if(action==='figure_pdf_prepare')return preparation.promise;if(action==='figure_pdf_inspect')return preview();return imported(2);};
  const tree=await mount(api,{onImported:items=>received.push(items)});
  assert.match(text(tree.toJSON()),/正在准备 PDF 导入/);assert.equal(readers.length,0);
  await act(async()=>{preparation.resolve({});await settle();});
  assert.deepEqual(calls.slice(0,2).map(call=>call.action),['figure_pdf_prepare','figure_pdf_inspect']);
  assert.deepEqual(checkboxes(tree).map(node=>node.props.checked),[true,false]);
  assert.doesNotMatch(text(tree.toJSON()),/private-token/);
  await click(button(tree,'清空'));assert.equal(button(tree,'导入 0 页').props.disabled,true);
  await act(async()=>{checkboxes(tree)[1].props.onChange({target:{checked:true}});tree.root.findByProps({value:'page'}).props.onChange();});
  await click(button(tree,'导入 1 页'));
  assert.deepEqual(calls.at(-1),{action:'figure_pdf_import',body:{token:'private-token',pages:[2],mode:'page'}});
  assert.equal(received.length,1);assert.equal(received[0][0].page,2);
  await unmount(tree);
});

test('preparation and import failures can be retried without losing the page selection',async()=>{
  let prepareCount=0,importCount=0,received=0;
  const tree=await mount(async action=>{
    if(action==='figure_pdf_prepare'){if(++prepareCount===1)throw Error('下载失败');return {};}
    if(action==='figure_pdf_inspect')return preview();
    if(++importCount===1)throw Error('临时失败');return imported();
  },{onImported:()=>received++});
  assert.match(text(tree.toJSON()),/下载失败/);await click(button(tree,'重试'));
  assert.equal(prepareCount,2);await click(button(tree,'导入 1 页'));
  assert.match(text(tree.toJSON()),/临时失败/);assert.equal(checkboxes(tree)[0].props.checked,true);
  await click(button(tree,'导入 1 页'));assert.equal(received,1);
  await unmount(tree);
});

test('closing during preparation prevents file reads and inspection even before the parent unmounts',async()=>{
  readers=[];const preparation=deferred(),calls=[];let closed=0;
  const tree=await mount(async action=>{calls.push(action);return preparation.promise;},{onClose:()=>closed++});
  await click(closeButton(tree));
  await act(async()=>{preparation.resolve({});await settle();});
  assert.equal(closed,1);assert.deepEqual(calls,['figure_pdf_prepare']);assert.equal(readers.length,0);
  await unmount(tree);
});

test('closing during file reading aborts the local reader and suppresses inspection',async()=>{
  readers=[];automaticRead=false;const calls=[];
  const tree=await mount(async action=>{calls.push(action);return {};});
  assert.match(text(tree.toJSON()),/正在读取页面预览/);assert.equal(readers.length,1);
  await click(closeButton(tree));assert.equal(readers[0].aborted,true);assert.deepEqual(calls,['figure_pdf_prepare']);
  await unmount(tree);automaticRead=true;
});

test('closing during import rejects late results and repeated clicks create only one request',async()=>{
  const result=deferred();let importedCount=0,requests=0;
  const tree=await mount(async action=>action==='figure_pdf_prepare'?{}:action==='figure_pdf_inspect'?preview():(requests++,result.promise),{onImported:()=>importedCount++});
  const importButton=button(tree,'导入 1 页');
  await act(async()=>{importButton.props.onClick();importButton.props.onClick();await settle();});
  assert.equal(requests,1);assert.match(text(tree.toJSON()),/正在导入所选页面/);
  await click(closeButton(tree));
  await act(async()=>{result.resolve(imported());await settle();});
  assert.equal(importedCount,0);await unmount(tree);
});

test('file changes discard old inspection and callback identity changes do not restart preparation',async()=>{
  const oldInspection=deferred();const calls=[];const secondFile={name:'second.pdf'};
  const api=async(action,body)=>{calls.push({action,body});return action==='figure_pdf_prepare'?{}:body.name==='paper.pdf'?oldInspection.promise:preview(3);};
  const props={file,api,onImported:()=>{},onClose:()=>{}};
  const tree=await mount(api);
  await act(async()=>{tree.update(React.createElement(FigurePdfImport,{...props,file:secondFile,api:(...args)=>api(...args)}));await settle();});
  assert.equal(checkboxes(tree).length,3);
  await act(async()=>{oldInspection.resolve(preview(1));await settle();});assert.equal(checkboxes(tree).length,3);
  await act(async()=>{tree.update(React.createElement(FigurePdfImport,{...props,file:secondFile,api:(...args)=>api(...args)}));await settle();});
  assert.equal(calls.filter(call=>call.action==='figure_pdf_prepare').length,2);await unmount(tree);
});

test('unmounting during import never calls the parent with a late result',async()=>{
  const result=deferred();let received=0;
  const tree=await mount(async action=>action==='figure_pdf_prepare'?{}:action==='figure_pdf_inspect'?preview():result.promise,{onImported:()=>received++});
  await click(button(tree,'导入 1 页'));await unmount(tree);
  await act(async()=>{result.resolve(imported());await settle();});assert.equal(received,0);
});

test('rejects invalid previews and empty imported results at the API boundary',async()=>{
  const invalid=preview();invalid.pages[0].preview='https://example.invalid/private.png';
  const tree=await mount(async action=>action==='figure_pdf_prepare'?{}:invalid);
  assert.match(text(tree.toJSON()),/无法读取 PDF 页面预览/);assert.equal(checkboxes(tree).length,0);await unmount(tree);
  let received=0;
  const empty=await mount(async action=>action==='figure_pdf_prepare'?{}:action==='figure_pdf_inspect'?preview():{items:[]},{onImported:()=>received++});
  await click(button(empty,'导入 1 页'));assert.match(text(empty.toJSON()),/PDF 导入结果不完整/);assert.equal(received,0);await unmount(empty);
});

test('all and clear work and selections above the backend limit are visibly blocked',async()=>{
  const tree=await mount(async action=>action==='figure_pdf_prepare'?{}:preview(21));
  assert.equal(tree.root.findByProps({value:'editable'}).props.checked,true);
  await click(button(tree,'全选'));assert.equal(checkboxes(tree).filter(node=>node.props.checked).length,21);
  assert.equal(button(tree,'导入 21 页').props.disabled,true);assert.match(text(tree.toJSON()),/每次最多导入 20 页/);
  await click(button(tree,'清空'));assert.equal(checkboxes(tree).filter(node=>node.props.checked).length,0);await unmount(tree);
});
