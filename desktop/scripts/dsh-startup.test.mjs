import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {patchStartupSource,patchBundleLoader,patchLayoutTitle,verifyDshStartup} from './patch-dsh-startup.mjs';

const runtime=new URL('../dsh-runtime/node_modules/',import.meta.url);
const boot=readFileSync(new URL('@deepseek-ai/dsh-web-frontend/dist/assets/index-BKQ_L1z6.js',runtime),'utf8');
const loader=readFileSync(new URL('@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-modules/lib/client.js',runtime),'utf8');
const originalBoot=readFileSync(new URL('./fixtures/dsh-boot-0.1.5.txt',import.meta.url),'utf8');
const originalLoader=readFileSync(new URL('./fixtures/dsh-loader-0.1.5.txt',import.meta.url),'utf8');

test('runtime title updates keep the OMaaX name after the boot page disappears',()=>{
  const source='const productTitle = "DeepSeek Harness"; document.title=productTitle;';
  const patched=patchLayoutTitle(source),document={};
  vm.runInNewContext(patched,{document});assert.equal(document.title,'OMaaX');
  assert.equal(patchLayoutTitle(patched),patched);
  assert.throws(()=>patchLayoutTitle('upstream changed'),/anchor changed/i);
});

test('staging patches and validates first-frame branding, reload UI, title and loader',()=>{
  assert.ok(boot.includes(patchStartupSource(originalBoot).split("\n").slice(1).join("\n").replace(";function by(){}","")));
  assert.equal(patchStartupSource(boot),boot);
  assert.ok(loader.includes(patchBundleLoader(originalLoader).split("/** Replace the rev query")[0]));
  assert.equal(patchBundleLoader(loader),loader);
  assert.throws(()=>patchStartupSource('changed upstream'),/anchor changed/);
  assert.throws(()=>patchBundleLoader('changed upstream'),/anchor changed/);
  verifyDshStartup();
});

class Element {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={setProperty(){}};this.textContent='';}
  append(...elements){for(const el of elements){el.parentElement=this;this.children.push(el);}}
  replaceChildren(...elements){this.children=[];this.append(...elements);}
  remove(){this.parentElement?.children.splice(this.parentElement.children.indexOf(this),1);}
  text(){return this.textContent+this.children.map(el=>el.text()).join(' ');}
}
function pageFixture(){
 const source=patchStartupSource(originalBoot),start=source.indexOf('var jy=class{'),end=source.indexOf(';function by()',start);
 assert.ok(start>0&&end>start);const logs=[],document={createElement:tag=>new Element(tag)};
 const ctx={document,console:{error:(...args)=>logs.push(args)},window:{location:{reload(){ctx.reloads++;}}},reloads:0,rt:new Proxy({},{get:(_,key)=>key})};
 ctx.ot=(cls,text)=>Object.assign(new Element('div'),{className:cls,textContent:text??''});
 const Boot=vm.runInNewContext(source.slice(start,end)+';jy',ctx),root=new Element('main'),page=new Boot(root);
 return{page,root,logs,ctx};
}
test('loading is branded from its first render, regardless of locale bootstrap',()=>{
 const{root,page}=pageFixture();assert.match(root.text(),/OMaaX.*正在启动智能体/s);assert.doesNotMatch(root.text(),/harness|deepseek|Loading plugins/i);
 page.setTotal(2);page.setState('@deepseek-ai/dsh-client-hmr','active');assert.doesNotMatch(root.text(),/deepseek/i);
 page.dispose();assert.equal(root.children.length,0);
});
test('failure and failed-entry-only branches show Chinese and retain diagnostics only in the log',()=>{
 for(const failure of [undefined,'failed to import loader entry (@deepseek-ai/dsh-client-hmr): /plugins/??secret failed to load']){
  const{root,page,logs,ctx}=pageFixture();if(failure)page.fail(failure);else page.setState('@deepseek-ai/dsh-client-hmr','failed');
  assert.match(root.text(),/智能体启动未完成/);assert.match(root.text(),/本地对话组件/);assert.doesNotMatch(root.text(),/deepseek|harness|secret|plugins|failed/i);
  assert.equal(logs.length,1);page.render();assert.equal(logs.length,1);
  const retry=page.card.children.find(el=>el.tag==='button');retry.onclick();assert.equal(ctx.reloads,1);assert.equal(retry.disabled,true);
 }
});
async function transport(outcomes){
 const appended=[],timers=[],src=patchBundleLoader(originalLoader),start=src.indexOf('const defaultLoadBundle = '),end=src.indexOf('\n\t\t/** Replace the rev query',start);
 const document={createElement:()=>({listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},remove(){this.removed=true;}}),head:{append(el){appended.push(el);queueMicrotask(()=>el.listeners[outcomes.shift()]());}}};
 const load=vm.runInNewContext(src.slice(start,end)+';defaultLoadBundle',{document,setTimeout(fn,ms){timers.push(ms);queueMicrotask(fn);}});
 let error;try{await load('/plugins/??unchanged&rev=original');}catch(e){error=e;}
 return{appended,timers,error};
}
test('a transient script transport failure is retried once with the same revision',async()=>{
 const result=await transport(['error','load']);assert.equal(result.error,undefined);assert.equal(result.appended.length,2);assert.deepEqual(result.timers,[300]);
 assert.ok(result.appended.every(el=>el.removed&&el.src==='/plugins/??unchanged&rev=original'));
});
test('success is never replayed; persistent failure remains bounded and diagnosable',async()=>{
 assert.equal((await transport(['load'])).appended.length,1);
 const failed=await transport(['error','error']);assert.equal(failed.appended.length,2);assert.match(failed.error.message,/bundle script.*failed to load/);
});
