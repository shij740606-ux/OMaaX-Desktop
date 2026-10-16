const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {Readable}=require('node:stream');
const {relay}=require('./relay.cjs');

function fixture(){
 const base=fs.mkdtempSync(path.join(os.tmpdir(),'omaax-relay-test-')),source=path.join(base,'source'),target=path.join(base,'target');fs.mkdirSync(source);fs.mkdirSync(target);
 const content=Buffer.from('simulation\n'.repeat(40000)),file=path.join(source,'reads.fastq');fs.writeFileSync(file,content);
 let fail=false;
 const sftp={lstat:(p,cb)=>fs.lstat(p,(e,s)=>{if(s)s.mtime=Math.floor(s.mtimeMs/1000);cb(e,s);}),
  createReadStream:(p,opts)=>{if(fail){fail=false;return Readable.from((async function*(){yield content.subarray(0,8192);throw Error('simulated disconnect');})());}return fs.createReadStream(p,opts);}};
 const request={files:[{name:'reads.fastq',path:file,size:content.length,mtime:Math.floor(fs.statSync(file).mtimeMs/1000)}],source:{},local:true,destination:target,resume:true,progressFile:path.join(base,'progress.json')};
 return {base,source,target,content,request,connect:async()=>({sftp,connection:{end(){}}}),breakNext(){fail=true;},dispose(){fs.rmSync(base,{recursive:true,force:true});}};
}
test('interrupted transfer retains exact progress; retry resumes and verifies without modifying source',async()=>{
 const f=fixture();try{
  f.breakNext();await assert.rejects(relay(f.request,f.connect),/disconnect/);
  assert.equal(JSON.parse(fs.readFileSync(f.request.progressFile)).state,'failed');
  const result=await relay(f.request,f.connect);assert.equal(result.files.length,1);
  assert.deepEqual(fs.readFileSync(path.join(f.target,'reads.fastq')),f.content);
  assert.deepEqual(fs.readFileSync(path.join(f.source,'reads.fastq')),f.content);
  assert.equal(JSON.parse(fs.readFileSync(f.request.progressFile)).state,'uploaded');
  const again=await relay(f.request,f.connect);assert.deepEqual(again,result);
 }finally{f.dispose();}
});
test('retry refuses unrelated existing content and changed source metadata',async()=>{
 const f=fixture();try{
  fs.writeFileSync(path.join(f.target,'reads.fastq'),'unrelated user content');
  await assert.rejects(relay(f.request,f.connect),/differs/);
  assert.equal(fs.readFileSync(path.join(f.target,'reads.fastq'),'utf8'),'unrelated user content');
  await assert.rejects(relay({...f.request,files:[{...f.request.files[0],mtime:0}]},f.connect),/Source changed/);
  assert.deepEqual(fs.readFileSync(path.join(f.source,'reads.fastq')),f.content);
 }finally{f.dispose();}
});
test('remote-to-remote streaming also resumes from a verified partial file',async()=>{
 const f=fixture();try{
  fs.writeFileSync(path.join(f.target,'reads.fastq'),f.content.subarray(0,100));
  const connect=async()=>{const v=await f.connect();v.sftp.createWriteStream=(p,o)=>fs.createWriteStream(p,o);v.sftp.mkdir=(p,o,cb)=>fs.mkdir(p,o,cb);return v;};
  const result=await relay({...f.request,local:false,target:{}},connect);
  assert.equal(result.files[0].size,f.content.length);assert.deepEqual(fs.readFileSync(path.join(f.target,'reads.fastq')),f.content);
 }finally{f.dispose();}
});
test('a second process cannot restart the same transfer while its owner is alive',async()=>{
 const f=fixture();try{
  let unlock;const running=relay(f.request,()=>new Promise(resolve=>{unlock=resolve;}));
  await assert.rejects(relay(f.request,f.connect),/仍在运行/);
  assert.equal(JSON.parse(fs.readFileSync(f.request.progressFile)).state,'running');
  unlock(await f.connect());await running;
  assert.deepEqual(fs.readFileSync(path.join(f.target,'reads.fastq')),f.content);
 }finally{f.dispose();}
});
