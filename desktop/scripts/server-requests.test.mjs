import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const module={exports:{}};
new Function('module','exports',ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/serverRequests.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(module,module.exports);
const {serverRequest,invalidateServer}=module.exports;
test('100 concurrent resource reads share one request',async()=>{
 let calls=0;const work=()=>new Promise(resolve=>setTimeout(()=>{calls++;resolve('metrics');},5));
 const values=await Promise.all(Array.from({length:100},()=>serverRequest('server-a','metrics',{},work)));
 assert.equal(calls,1);assert.equal(values.length,100);
});
test('mixed load stays within four requests and mutations are not retried',async()=>{
 let active=0,peak=0,calls=0;
 await Promise.all(Array.from({length:60},(_,i)=>serverRequest('server-'+i,'server_write',{},async()=>{calls++;active++;peak=Math.max(peak,active);await new Promise(r=>setTimeout(r,1));active--;return i;})));
 assert.equal(peak,4);assert.equal(calls,60);
 let failures=0;await assert.rejects(serverRequest('failure','server_write',{},async()=>{failures++;throw Error('offline');}));assert.equal(failures,1);
});
test('mutation invalidates cached state and late reads cannot repopulate it',async()=>{
 let resolve;const old=serverRequest('stale','status',{},()=>new Promise(r=>{resolve=r;}));
 await serverRequest('stale','configure',{},async()=>true);resolve('old');assert.equal(await old,'old');
 assert.equal(await serverRequest('stale','status',{},async()=>'current'),'current');
 invalidateServer('stale');assert.equal(await serverRequest('stale','status',{},async()=>'refreshed'),'refreshed');
});
test('large transfers cannot occupy the slots needed to refresh progress and cancel tasks',async()=>{
 let release,active=0,peak=0;const gate=new Promise(resolve=>{release=resolve;});
 const work=Array.from({length:4},(_,i)=>serverRequest('bulk-'+i,'transfer',{},async()=>{active++;peak=Math.max(peak,active);await gate;active--;return true;}));
 try{
  assert.equal(peak,2);
  const value=await Promise.race([serverRequest('progress','transfers',{},async()=>'progress updated'),new Promise((_,reject)=>setTimeout(()=>reject(Error('progress blocked by transfers')),500))]);
  assert.equal(value,'progress updated');
  assert.equal(await serverRequest('cancel','cancel',{},async()=>'cancel accepted'),'cancel accepted');
 }finally{release();await Promise.all(work);}
 assert.equal(peak,2);
});
