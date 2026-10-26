import test from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {mkdtempSync,mkdirSync,writeFileSync,rmSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {protect,protectEsm,stage} from './protect-local-code.mjs';
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

test('protected ESM preserves relative modules, file paths and direct CLI execution',()=>{
 const dir=mkdtempSync(path.join(tmpdir(),'omaax-esm-code-')),key=randomBytes(32);
 try{
  const source="import {value} from './value.mjs';\nimport {fileURLToPath} from 'node:url';\nexport const answer=value+1;\nexport function location(){return fileURLToPath(import.meta.url);}\nif(process.argv[1]===fileURLToPath(import.meta.url))console.log(JSON.stringify({answer,file:location()}));\n";
  const wrapper=protectEsm(Buffer.from(source),key),file=path.join(dir,'main.mjs');
  assert.ok(!wrapper.includes(source));assert.ok(!wrapper.includes(key.toString('hex')));
  writeFileSync(file,wrapper);writeFileSync(path.join(dir,'value.mjs'),protectEsm(Buffer.from('export const value=41;\n'),key));
  const env={...process.env,OMAAX_LOCAL_CODE_KEY:key.toString('hex')};
  const direct=JSON.parse(execFileSync(process.execPath,[file],{env,encoding:'utf8',stdio:['ignore','pipe','pipe']}));
  assert.equal(direct.answer,42);assert.equal(direct.file,file);
  const imported=JSON.parse(execFileSync(process.execPath,['--input-type=module','-e',"import {pathToFileURL} from 'node:url'; const m=await import(pathToFileURL(process.argv[2]));console.log(JSON.stringify({answer:m.answer,file:m.location()}));",'fixture-importer',file],{env,encoding:'utf8',stdio:['ignore','pipe','pipe']}));
  assert.deepEqual(imported,direct);
  assert.throws(()=>execFileSync(process.execPath,[file],{env:{...env,OMAAX_LOCAL_CODE_KEY:randomBytes(32).toString('hex')},stdio:['ignore','pipe','pipe']}));
  writeFileSync(file,wrapper.replace(/_payload=Buffer.from\('([A-Za-z0-9+/=]+)'/,(_,encoded)=>{const b=Buffer.from(encoded,'base64');b[b.length-1]^=1;return `_payload=Buffer.from('${b.toString('base64')}'`;}));
  assert.throws(()=>execFileSync(process.execPath,[file],{env,stdio:['ignore','pipe','pipe']}));
  for(const unsupported of ['export let changing=1;','export * from "./value.mjs";','export default function main(){}'])assert.throws(()=>protectEsm(Buffer.from(unsupported),key));
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test('staging protects shipped document modules while preserving third-party files and notices',()=>{
 const dir=mkdtempSync(path.join(tmpdir(),'omaax-document-protection-'));
 try{
  const source=path.join(dir,'source'),destination=path.join(dir,'protected');
  mkdirSync(path.join(source,'node_modules','fixture'),{recursive:true});
  for(const name of ['index.mjs','cli.mjs'])writeFileSync(path.join(source,name),readFileSync(new URL(`../resources/documents/${name}`,import.meta.url)));
  writeFileSync(path.join(source,'node_modules','fixture','index.js'),'third party unchanged');
  writeFileSync(path.join(source,'THIRD_PARTY_NOTICES.md'),'original license notices');
  const keyFile=path.join(dir,'key'),rustFile=path.join(dir,'key.rs');
  assert.equal(stage(source,destination,keyFile,rustFile),2);
  assert.equal(readFileSync(path.join(destination,'node_modules','fixture','index.js'),'utf8'),'third party unchanged');
  assert.equal(readFileSync(path.join(destination,'THIRD_PARTY_NOTICES.md'),'utf8'),'original license notices');
  assert.ok(!readFileSync(path.join(destination,'cli.mjs'),'utf8').includes('function isolatedPythonEnv'));
  const env={...process.env,OMAAX_LOCAL_CODE_KEY:readFileSync(keyFile,'utf8').trim()};
  const loaded=JSON.parse(execFileSync(process.execPath,['--input-type=module','-e',"import {pathToFileURL} from 'node:url'; const host=await import(pathToFileURL(process.argv[1]));const cli=await import(pathToFileURL(process.argv[2]));console.log(JSON.stringify({name:host.name,apply:typeof host.apply,command:cli.parseArguments(['prepare']).command}));",path.join(destination,'index.mjs'),path.join(destination,'cli.mjs')],{env,encoding:'utf8',stdio:['ignore','pipe','pipe']}));
  assert.deepEqual(loaded,{name:'omaax-documents',apply:'function',command:'prepare'});
 }finally{rmSync(dir,{recursive:true,force:true});}
});
