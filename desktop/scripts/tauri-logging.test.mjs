import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

test('unauthenticated or failed logging never produces another unhandled rejection',async()=>{
  const source=readFileSync(new URL('../src/renderer/src/tauri-bridge.ts',import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  const listeners=new Map(),calls=[],exports={};
  const imports={
    '@tauri-apps/api/core':{invoke:(command)=>{calls.push(command);return Promise.reject('请登录');}},
    '@tauri-apps/api/event':{},
    './dshLayoutQueue':{dshLayoutQueue:()=>()=>{}},
    './serverRequests':{},
  };
  vm.runInNewContext(code,{
    exports,require:name=>{assert.ok(name in imports,name);return imports[name];},
    window:{addEventListener:(name,fn)=>listeners.set(name,fn)},
    document:{documentElement:{},querySelectorAll:()=>[]},
    MutationObserver:class {observe(){}},console,
  });
  exports.installTauriBridge();
  listeners.get('error')({message:'test failure',filename:'test',lineno:1});
  listeners.get('unhandledrejection')({reason:'original failure'});
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.deepEqual(calls,['client_log','client_log','client_log']);
});
