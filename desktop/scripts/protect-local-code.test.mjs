import test from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {mkdtempSync,writeFileSync,rmSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {protect} from './protect-local-code.mjs';
test('encrypted CommonJS preserves relative imports and exports; rejects wrong keys and tampering',()=>{
 const dir=mkdtempSync(path.join(tmpdir(),'omaax-code-'));
 try{
  const key=randomBytes(32),source=Buffer.from("module.exports=require('./value.cjs')+1;");
  const wrapper=protect(source,key),file=path.join(dir,'main.cjs');
  assert.ok(!wrapper.includes(source.toString()));assert.ok(!wrapper.includes(key.toString('hex')));
  writeFileSync(file,wrapper);writeFileSync(path.join(dir,'value.cjs'),'module.exports=41;');
  const run=k=>execFileSync(process.execPath,['-e','console.log(require(process.argv[1]))',file],{env:{...process.env,OMAAX_LOCAL_CODE_KEY:k},stdio:['ignore','pipe','pipe']}).toString().trim();
  assert.equal(run(key.toString('hex')),'42');
  assert.throws(()=>run(randomBytes(32).toString('hex')));
  assert.throws(()=>run(''));
  writeFileSync(file,wrapper.replace(/payload=Buffer.from\('([A-Za-z0-9+/=]+)'/,(_,encoded)=>{const b=Buffer.from(encoded,'base64');b[b.length-1]^=1;return `payload=Buffer.from('${b.toString('base64')}'`;}));
  assert.throws(()=>run(key.toString('hex')));
 }finally{rmSync(dir,{recursive:true,force:true});}
});
test('protected real connection module can be loaded',()=>{
 const dir=mkdtempSync(path.join(tmpdir(),'omaax-real-code-')),key=randomBytes(32);
 try{
  const source=readFileSync(new URL('../resources/ssh/connection-pool.cjs',import.meta.url));
  const file=path.join(dir,'pool.cjs');writeFileSync(file,protect(source,key));
  execFileSync(process.execPath,['-e','require(process.argv[1])',file],{env:{...process.env,OMAAX_LOCAL_CODE_KEY:key.toString('hex'),NODE_PATH:fileURLToPath(new URL('../resources/ssh/node_modules',import.meta.url))},stdio:['ignore','pipe','pipe']});
 }finally{rmSync(dir,{recursive:true,force:true});}
});
