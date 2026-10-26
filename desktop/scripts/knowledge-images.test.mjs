import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),React=require('react'),{act,create}=require('react-test-renderer');
const module={exports:{}};
const code=ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/views/KnowledgeMarkdown.tsx',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
new Function('require','module','exports',code)(n=>n==='../i18n'?{useT:()=>({lang:'zh',t:value=>value})}:n==='react-markdown'?({components,children})=>React.createElement(components.img,{src:children}):n==='remark-gfm'?()=>null:require(n),module,module.exports);
const {KnowledgeImage,copyKnowledgeImage,default:KnowledgeMarkdown}=module.exports;
const text=n=>typeof n==='string'?n:(n.children||[]).map(text).join('');
test('image copy writes PNG bytes; save uses the loaded image and handles cancel and failure',async()=>{
 let written,saved,fail=false,cancel=false;
 globalThis.Image=class{naturalWidth=10;naturalHeight=10;async decode(){}};
 globalThis.document={createElement:()=>({getContext:()=>({drawImage(){}}),toBlob:cb=>cb(new Blob(['png'],{type:'image/png'}))})};
 globalThis.ClipboardItem=class{constructor(data){this.data=data;}};
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{clipboard:{write:async items=>{written=items;}}}});
 await copyKnowledgeImage('data:image/png;base64,eA==');assert.equal(written[0].data['image/png'].type,'image/png');
 globalThis.window={biodsh:{cloudQa:async(action,body)=>{if(action==='image')return {src:'data:image/png;base64,eA=='};saved=body;if(fail)throw Error('disk');return {saved:!cancel};}}};
 let view;await act(async()=>{view=create(React.createElement(KnowledgeImage,{src:'/api/figures/proxy?x=1'}));});
 const button=label=>view.root.findAllByType('button').find(n=>text(n)===label);
 await act(async()=>button('复制图片').props.onClick());assert.match(text(view.root),/图片已复制/);
 await act(async()=>button('保存图片').props.onClick());assert.equal(saved.data,'data:image/png;base64,eA==');assert.match(text(view.root),/图片已保存/);
 cancel=true;await act(async()=>button('保存图片').props.onClick());assert.doesNotMatch(text(view.root),/图片已保存/);
 fail=true;await act(async()=>button('保存图片').props.onClick());assert.match(text(view.root),/保存失败/);
 await act(async()=>view.unmount());
});

test('image requests carry the selected session and stale results cannot replace another conversation',async()=>{
 const pending=[],calls=[];
 globalThis.window={biodsh:{cloudQa:(action,body)=>{calls.push({action,body});return new Promise(resolve=>pending.push(resolve));}}};
 let view;await act(async()=>{view=create(React.createElement(KnowledgeMarkdown,{text:'/api/figures/proxy?sig=old',sessionId:'session-a'}));});
 await act(async()=>view.update(React.createElement(KnowledgeMarkdown,{text:'/api/figures/proxy?sig=old',sessionId:'session-b'})));
 assert.deepEqual(calls.map(c=>c.body.sessionId),['session-a','session-b']);
 await act(async()=>pending[0]({src:'data:image/png;base64,YQ==',persisted:true}));
 assert.equal(view.root.findAllByType('img').length,0);
 await act(async()=>pending[1]({src:'data:image/png;base64,Yg==',persisted:true}));
 assert.equal(view.root.findByType('img').props.src,'data:image/png;base64,Yg==');
 await act(async()=>view.unmount());
});

test('failed conversation image persistence is visible and can be retried',async()=>{
 let persisted=false,calls=0;
 globalThis.window={biodsh:{cloudQa:async()=>{calls++;return {src:'data:image/png;base64,eA==',persisted};}}};
 let view;await act(async()=>{view=create(React.createElement(KnowledgeMarkdown,{text:'/api/figures/proxy?x=1',sessionId:'session-a'}));});
 assert.match(text(view.root),/未能随会话保存到本机/);assert.equal(view.root.findAllByType('img').length,1);
 persisted=true;await act(async()=>view.root.findAllByType('button').find(b=>text(b)==='重试保留图片').props.onClick());
 assert.equal(calls,2);assert.doesNotMatch(text(view.root),/未能随会话保存到本机/);
 await act(async()=>view.unmount());
});

test('all session images start native retention immediately and survive view unmount',async()=>{
 const calls=[],pending=[];
 globalThis.window={biodsh:{cloudQa:(action,body)=>{calls.push({action,body});return new Promise(resolve=>pending.push(resolve));}}};
 let view;await act(async()=>{view=create(React.createElement(React.Fragment,null,...Array.from({length:6},(_,i)=>React.createElement(KnowledgeMarkdown,{key:i,text:'/api/figures/proxy?image='+i,sessionId:'session-a'}))));});
 assert.equal(calls.length,6);assert.ok(calls.every(call=>call.body.sessionId==='session-a'));
 await act(async()=>view.unmount());
 await act(async()=>pending.forEach(resolve=>resolve({src:'data:image/png;base64,eA==',persisted:true})));
 assert.equal(calls.length,6);
});

test('an uncached expired image explains recovery without claiming it was saved',async()=>{
 globalThis.window={biodsh:{cloudQa:async()=>{throw Error('IMAGE_DENIED');}}};
 let view;await act(async()=>{view=create(React.createElement(KnowledgeMarkdown,{text:'/api/figures/proxy?expired=1',sessionId:'session-a'}));});
 assert.match(text(view.root),/尚未保存在本机/);assert.match(text(view.root),/请重新查询/);assert.equal(view.root.findAllByType('img').length,0);
 await act(async()=>view.unmount());
});

test('retry after browser decode failure requests a fresh image instead of reusing bad cached bytes',async()=>{
 const calls=[];globalThis.window={biodsh:{cloudQa:async(action,body)=>{calls.push(body);return {src:'data:image/png;base64,eA==',persisted:true};}}};
 let view;await act(async()=>{view=create(React.createElement(KnowledgeMarkdown,{text:'/api/figures/proxy?x=1',sessionId:'session-a'}));});
 assert.equal(calls[0].refresh,undefined);
 await act(async()=>view.root.findByType('img').props.onError());
 await act(async()=>view.root.findAllByType('button').find(b=>text(b)==='重新加载图片').props.onClick());
 assert.equal(calls[1].refresh,true);
 await act(async()=>view.root.findByType('img').props.onLoad());
 await act(async()=>view.update(React.createElement(KnowledgeMarkdown,{text:'/api/figures/proxy?x=2',sessionId:'session-b'})));
 assert.equal(calls[2].refresh,undefined);
 await act(async()=>view.unmount());
});
