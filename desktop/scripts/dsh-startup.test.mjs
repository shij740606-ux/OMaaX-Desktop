import {test} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import vm from 'node:vm';
import {patchStartupSource,patchBundleLoader,patchLayoutTitle,verifyDshStartup} from './patch-dsh-startup.mjs';

const runtime=new URL('../dsh-runtime/node_modules/',import.meta.url);
const frontend=new URL('@deepseek-ai/dsh-web-frontend/dist/',runtime);
const entry=readFileSync(new URL('index.html',frontend),'utf8').match(/<script\b[^>]*\bsrc="([^"]+)"/)?.[1];
assert.ok(entry,'Packaged boot entry must be declared in index.html');
const boot=readFileSync(new URL(entry,frontend),'utf8');
const loaderPath=['@deepseek-ai/dsh-client-modules/lib/client.js','@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-modules/lib/client.js'].map(file=>new URL(file,runtime)).find(existsSync);
assert.ok(loaderPath,'Actual module loader must be installed');
const loader=readFileSync(loaderPath,'utf8');
const originalBoot=readFileSync(new URL('./fixtures/dsh-boot-0.1.5.txt',import.meta.url),'utf8');
const originalLoader=readFileSync(new URL('./fixtures/dsh-loader-0.1.5.txt',import.meta.url),'utf8');
// Exact boot class from the pinned 0.2.1-rc.2 distribution.
const originalBootV2="var CZ=class{root;card;wordmark;spinner;hint;states=new Map;active=new Set;total=0;failure;constructor(e){this.root=Yn(Kn.boot),this.root.dataset.dshBoot=\"\",this.card=Yn(Kn.card),this.wordmark=Yn(Kn.wordmark,\"HARNESS\"),this.spinner=Yn(Kn.spinner),this.spinner.dataset.dshBootSpinner=\"\",this.hint=Yn(Kn.hint,\"Loading plugins…\"),this.card.append(this.wordmark,this.spinner,this.hint),this.root.append(this.card),e.append(this.root),this.updateProgress()}setTotal(e){this.total=e,this.updateProgress()}setState(e,t){this.states.set(e,t),t===\"active\"&&this.active.add(e),this.updateProgress(),this.render()}fail(e){this.failure=e,this.render()}dispose(){this.root.remove()}render(){const e=[...this.states].filter(([,r])=>r===\"failed\").map(([r])=>r);if(this.failure===void 0&&e.length===0){this.spinner.parentElement!==this.card&&this.card.replaceChildren(this.wordmark,this.spinner,this.hint);return}const t=Yn(Kn.failed);t.append(Yn(Kn.failedTitle,\"Failed to load plugins\"));for(const r of e)t.append(Yn(Kn.failedItem,r));this.failure!==void 0&&t.append(Yn(Kn.failedItem,this.failure)),this.card.replaceChildren(this.wordmark,t)}updateProgress(){const e=this.total===0?0:Math.min(this.active.size/this.total,1);this.spinner.style.setProperty(\"--dsh-boot-arc\",`${String(Math.round(72+e*216))}deg`)}};";
const bootFixtures=[{version:"0.1",source:originalBoot,className:"jy",element:"ot",styles:"rt"},{version:"0.2",source:originalBootV2,className:"CZ",element:"Yn",styles:"Kn"}];

test('runtime title updates keep the OMaaX name after the boot page disappears',()=>{
  const source='const productTitle = "DeepSeek Harness"; document.title=productTitle;';
  const patched=patchLayoutTitle(source),document={};
  vm.runInNewContext(patched,{document});assert.equal(document.title,'OMaaX');
  assert.equal(patchLayoutTitle(patched),patched);
  assert.throws(()=>patchLayoutTitle('upstream changed'),/anchor changed/i);
});

test('staging patches and validates first-frame branding, reload UI, title and loader',()=>{
  assert.ok(boot.includes('/*OMAAX_DSH_STARTUP_V1*/'));
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
function pageFixture(fixture){
 const source=patchStartupSource(fixture.source),logs=[],document={createElement:tag=>new Element(tag)};
 const ctx={document,console:{error:(...args)=>logs.push(args)},window:{location:{reload(){ctx.reloads++;}}},reloads:0};
 ctx[fixture.styles]=new Proxy({},{get:(_,key)=>key});
 ctx[fixture.element]=(cls,text)=>Object.assign(new Element('div'),{className:cls,textContent:text??''});
 const Boot=vm.runInNewContext(source+';'+fixture.className,ctx),root=new Element('main'),page=new Boot(root);
 return{page,root,logs,ctx};
}
for(const fixture of bootFixtures){
test(fixture.version+' loading is branded from its first render, regardless of locale bootstrap',()=>{
 const{root,page}=pageFixture(fixture);assert.match(root.text(),/OMaaX.*正在启动智能体/s);assert.doesNotMatch(root.text(),/harness|deepseek|Loading plugins/i);
 page.setTotal(2);page.setState('@deepseek-ai/dsh-client-hmr','active');assert.doesNotMatch(root.text(),/deepseek/i);
 page.dispose();assert.equal(root.children.length,0);
});
test(fixture.version+' failure and failed-entry-only branches show Chinese and retain diagnostics only in the log',()=>{
 for(const failure of [undefined,'failed to import loader entry (@deepseek-ai/dsh-client-hmr): /plugins/??secret failed to load']){
  const{root,page,logs,ctx}=pageFixture(fixture);if(failure)page.fail(failure);else page.setState('@deepseek-ai/dsh-client-hmr','failed');
  assert.match(root.text(),/智能体启动未完成/);assert.match(root.text(),/本地对话组件/);assert.doesNotMatch(root.text(),/deepseek|harness|secret|plugins|failed/i);
  assert.equal(logs.length,1);page.render();assert.equal(logs.length,1);
  const retry=page.card.children.find(el=>el.tag==='button');retry.onclick();assert.equal(ctx.reloads,1);assert.equal(retry.disabled,true);
 }
});
}
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
