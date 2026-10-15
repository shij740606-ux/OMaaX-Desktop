import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';
const module={exports:{}};
new Function('exports','module',ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/views/workflowPlanRequest.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText)(module.exports,module);
const {completePendingPlan}=module.exports;
function store(){const data=new Map();return{getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};}
const result={plan:{id:'created'},digest:'a'.repeat(64)};
const pending=(s,k,id='env')=>s.setItem(k,JSON.stringify({id,body:{workflow:'rnaseq',steps:['rnaseq_qc']}}));
test('keeps pending until a successful response and shares in-flight requests across page remounts',async()=>{
 const s=store(),key='remount';pending(s,key);let release,calls=0;
 const create=body=>{calls++;assert.equal(body.workflow,'rnaseq');return new Promise(resolve=>release=resolve);};
 const first=completePendingPlan(s,key,'env',create),second=completePendingPlan(s,key,'env',create);
 await Promise.resolve();assert.equal(calls,1);assert.ok(s.getItem(key));release(result);
 assert.deepEqual(await first,result);assert.deepEqual(await second,result);assert.equal(s.getItem(key),null);
});
test('retains readable failure across navigation without silently retrying every poll',async()=>{
 const s=store(),key='failure';pending(s,key);let calls=0;
 await assert.rejects(completePendingPlan(s,key,'env',()=>{calls++;throw Error('缺少样本分组');}),/缺少样本分组/);
 assert.equal(JSON.parse(s.getItem(key)).error,'缺少样本分组');
 await assert.rejects(completePendingPlan(s,key,'env',()=>{calls++;return result;}),/缺少样本分组/);assert.equal(calls,1);
 pending(s,key);assert.deepEqual(await completePendingPlan(s,key,'env',async()=>result),result);
});
test('does not erase a newer selection when an earlier request finishes',async()=>{
 const s=store(),key='new-selection';pending(s,key);let release;
 const old=completePendingPlan(s,key,'env',()=>new Promise(resolve=>release=resolve));await Promise.resolve();
 pending(s,key,'new-env');release(result);await old;assert.equal(JSON.parse(s.getItem(key)).id,'new-env');
 assert.deepEqual(await completePendingPlan(s,key,'new-env',async()=>result),result);
});
test('ignores unrelated environment tasks and reports malformed server responses',async()=>{
 const s=store(),key='invalid';pending(s,key);
 assert.equal(await completePendingPlan(s,key,'other',()=>{throw Error('must not run');}),null);
 await assert.rejects(completePendingPlan(s,key,'env',async()=>({})),/有效的执行计划/);
 assert.match(JSON.parse(s.getItem(key)).error,/有效的执行计划/);
});
test('different inputs sharing an environment task keep separate plan requests',async()=>{
 const s=store(),key='same-environment';pending(s,key);let release;
 const old=completePendingPlan(s,key,'env',()=>new Promise(resolve=>release=resolve));await Promise.resolve();
 const next={id:'env',body:{workflow:'rnaseq',input:'/new-data'}};s.setItem(key,JSON.stringify(next));
 const answer={plan:{id:'new-plan'},digest:'b'.repeat(64)};
 assert.deepEqual(await completePendingPlan(s,key,'env',async body=>{assert.equal(body.input,'/new-data');return answer;}),answer);
 release(result);await old;assert.equal(s.getItem(key),null);
});
