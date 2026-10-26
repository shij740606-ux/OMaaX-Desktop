import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function bridge(invoke){
  const source=readFileSync(new URL('../src/renderer/src/tauri-bridge.ts',import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  const exports={},window={addEventListener(){}},channels=[];
  const imports={
    '@tauri-apps/api/core':{invoke:(command,args)=>command==='client_log'?Promise.resolve():invoke(command,args),Channel:class {constructor(){channels.push(this);}}},
    '@tauri-apps/api/event':{},'./dshLayoutQueue':{dshLayoutQueue:()=>()=>{}},'./serverRequests':{},
    './window-drag-regions':{installWindowDragRegions:()=>{}},
  };
  vm.runInNewContext(code,{exports,require:name=>{assert.ok(name in imports,name);return imports[name];},window,
    document:{documentElement:{},querySelectorAll:()=>[]},MutationObserver:class {observe(){}},console});
  exports.installTauriBridge();return {api:window.biodsh,channels};
}

test('QA event channel delivers progress and stops callbacks after completion or error',async()=>{
  for(const fail of [false,true]){
    let finish,args;const received=[];
    const {api,channels}=bridge((command,body)=>{assert.equal(command,'cloud_qa');args=body;return new Promise((resolve,reject)=>finish=()=>fail?reject(Error('offline')):resolve({answer:'final'}));});
    const result=api.cloudQa('chat',{question:'test',requestId:'test-request'},event=>received.push(event));
    assert.equal(channels.length,1);assert.equal(args.onEvent,channels[0]);assert.equal(args.action,'chat');
    const event={event:'progress',payload:{stage:'entities_identified'}};
    channels[0].onmessage(event);assert.deepEqual(received,[event]);
    finish();if(fail)await assert.rejects(result,/offline/);else assert.equal((await result).answer,'final');
    channels[0].onmessage({event:'answer_delta',payload:{content:'late'}});assert.equal(received.length,1);
  }
});

test('search, history and cancel preserve their existing nonstream invocation',async()=>{
  const calls=[];const {api,channels}=bridge(async(command,body)=>{calls.push({command,body});return {ok:true};});
  for(const action of ['search','list','detail','message','cancel'])await api.cloudQa(action,{requestId:'test'});
  assert.equal(channels.length,0);assert.equal(calls.length,5);
  assert.ok(calls.every(c=>c.command==='cloud_qa'&&!('onEvent' in c.body)));
});
