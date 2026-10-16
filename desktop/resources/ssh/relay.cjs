// Stream between separately authenticated SSH accounts; never forward credentials to a server.
const { Client } = require('ssh2');
const { createHash,randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { Transform, Writable } = require('node:stream');
const { pipeline } = require('node:stream/promises');
function safeName(name) {
  if (typeof name !== 'string' || !name || name.includes('\\') || name.includes(':') || name.includes('\0') || name.startsWith('/') || name.split('/').some(p => !p || p === '.' || p === '..')) throw Error('Invalid transfer path');
  return name;
}
function open(credentials) {
  return new Promise((resolve, reject) => {
    const connection = new Client();
    connection.on('error', reject);
    connection.on('ready', () => connection.sftp((error, sftp) => error ? (connection.end(), reject(error)) : resolve({ connection, sftp })));
    connection.connect({ ...credentials, readyTimeout:15000, keepaliveInterval:15000, keepaliveCountMax:4, hostVerifier:key => key.toString('base64') === credentials.hostKey });
  });
}
const stat = (sftp,p) => new Promise((resolve,reject) => sftp.lstat(p,(e,v)=> e?reject(e):resolve(v)));
function lease(progressFile){
  if(!progressFile)return ()=>{};
  const file=progressFile+'.lock',value=JSON.stringify({pid:process.pid,token:randomUUID()});
  try{const fd=fs.openSync(file,'wx',0o600);try{fs.writeFileSync(fd,value);}finally{fs.closeSync(fd);}}
  catch(error){
    if(error.code!=='EEXIST')throw error;
    let owner;try{owner=JSON.parse(fs.readFileSync(file,'utf8'));}catch{throw Error('传输正在准备，请稍后重试');}
    try{process.kill(owner.pid,0);throw Error('同一传输仍在运行，请等待或刷新进度');}
    catch(e){if(e.code!=='ESRCH')throw e;}
    fs.unlinkSync(file);const fd=fs.openSync(file,'wx',0o600);try{fs.writeFileSync(fd,value);}finally{fs.closeSync(fd);}
  }
  return ()=>{try{if(fs.readFileSync(file,'utf8')===value)fs.unlinkSync(file);}catch{}};
}
async function mkdir(sftp, dir) {
  try { await new Promise((resolve,reject)=>sftp.mkdir(dir,{mode:0o700},e=>e?reject(e):resolve())); }
  catch (error) { const info=await stat(sftp,dir); if(!info.isDirectory() || info.isSymbolicLink()) throw error; }
}
async function relay(request, connect=open) {
  let source, target;
  const release=lease(request.progressFile);
  const results=[];let current='',copied=0,lastWrite=0;
  const total=(request.files||[]).reduce((n,f)=>n+f.size,0);
  const progress=(state,detail,force=false)=>{
    if(!request.progressFile || !force&&Date.now()-lastWrite<250)return;
    lastWrite=Date.now();const p=request.progressFile,temp=p+'.tmp';
    fs.writeFileSync(temp,JSON.stringify({state,detail,current,bytes:copied,total,completed:results.length,count:request.files.length,files:results,updated:Date.now()/1000}),{mode:0o600});fs.renameSync(temp,p);
  };
  async function digest(stream,hash=createHash('sha256')){
    stream.on('error',()=>{});
    await pipeline(stream,new Writable({write(chunk,encoding,done){hash.update(chunk);progress('running','正在校验 '+current);done();}}));return hash.digest('hex');
  }
  try {
    if (!Array.isArray(request.files) || request.files.length>500) throw Error('Too many files');
    request.files.forEach(file=>safeName(file.name));
    if(new Set(request.files.map(f=>f.name)).size!==request.files.length)throw Error('Duplicate transfer path');
    progress('running','正在连接来源与目标',true);
    source=await connect(request.source);
    if(!request.local) target=await connect(request.target);
    for(const file of request.files) {
      current=file.name;
      const before=await stat(source.sftp,file.path);
      if(!before.isFile() || before.isSymbolicLink() || before.size!==file.size || file.mtime!==undefined&&file.mtime!==before.mtime) throw Error('Source changed before transfer');
      const dest=request.local ? path.join(request.destination,file.name) : path.posix.join(request.destination,file.name);
      const parts=file.name.split('/').slice(0,-1); let parent=request.destination;
      for(const part of parts) {
        parent=request.local?path.join(parent,part):path.posix.join(parent,part);
        if(request.local) { fs.mkdirSync(parent,{recursive:true}); if(fs.lstatSync(parent).isSymbolicLink())throw Error('Symlink destination'); }
        else await mkdir(target.sftp,parent);
      }
      let existing;
      try{existing=request.local?fs.lstatSync(dest):await stat(target.sftp,dest);}catch(error){if(error.code!==2&&error.code!=='ENOENT')throw error;}
      if(existing&&(!request.resume||!existing.isFile()||existing.isSymbolicLink()||existing.size>file.size))throw Error('Destination exists or changed; refusing to overwrite');
      let size=existing?.size||0;const offset=size;
      if(offset){
        progress('running','正在核对中断位置 '+current,true);
        const a=await digest(source.sftp.createReadStream(file.path,{start:0,end:offset-1}));
        const b=await digest(request.local?fs.createReadStream(dest):target.sftp.createReadStream(dest));
        if(a!==b)throw Error('Partial destination differs from source; refusing to overwrite');
        copied+=offset;
      }
      if(!existing||offset<file.size){
      const checksum=new Transform({transform(chunk,encoding,cb){size+=chunk.length;copied+=chunk.length;progress('running','正在复制 '+current);cb(null,chunk);}});
      const opts={flags:existing?'r+':'wx',start:offset,mode:0o600};
      const output=request.local?fs.createWriteStream(dest,opts):target.sftp.createWriteStream(dest,opts);
      const input=source.sftp.createReadStream(file.path,{start:offset});
      // Keep a listener during SSH teardown, after pipeline has already reported a failure.
      input.on('error',()=>{});output.on('error',()=>{});
      await pipeline(input,checksum,output);
      }
      const after=await stat(source.sftp,file.path);
      if(size!==file.size || before.size!==after.size || before.mtime!==after.mtime)throw Error('Source changed during transfer; destination not registered');
      progress('running','正在校验来源与目标 '+current,true);
      const sha256=await digest(source.sftp.createReadStream(file.path));
      const actual=await digest(request.local?fs.createReadStream(dest):target.sftp.createReadStream(dest));
      const final=await stat(source.sftp,file.path);
      if(sha256!==actual||before.size!==final.size||before.mtime!==final.mtime)throw Error('Transfer checksum changed; destination not registered');
      results.push({name:file.name,size,sha256});
      progress('running','文件已校验 '+current,true);
    }
    progress('uploaded','文件复制完成，等待最终登记',true);
    return {files:results};
  } catch(error){progress('failed',error.message,true);throw error;}
  finally { source?.connection.end(); target?.connection.end(); release(); }
}
if(require.main===module){
  let input='';process.stdin.setEncoding('utf8');
  process.stdin.on('data',chunk=>{input+=chunk;if(input.length>2*1024*1024)process.exit(1);});
  process.stdin.on('end',()=>{const timer=setTimeout(()=>process.exit(1),12*3600000);Promise.resolve().then(()=>relay(JSON.parse(input))).then(result=>process.stdout.write(JSON.stringify(result)),error=>process.stdout.write(JSON.stringify({error:error.message}))).finally(()=>clearTimeout(timer));});
}
module.exports={safeName,relay};
