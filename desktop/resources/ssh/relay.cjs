// Stream between separately authenticated SSH accounts; never forward credentials to a server.
const { Client } = require('ssh2');
const { createHash } = require('node:crypto');
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
async function mkdir(sftp, dir) {
  try { await new Promise((resolve,reject)=>sftp.mkdir(dir,{mode:0o700},e=>e?reject(e):resolve())); }
  catch (error) { const info=await stat(sftp,dir); if(!info.isDirectory() || info.isSymbolicLink()) throw error; }
}
async function relay(request) {
  let source, target;
  try {
    if (!Array.isArray(request.files) || request.files.length>500) throw Error('Too many files');
    request.files.forEach(file=>safeName(file.name));
    source=await open(request.source);
    if(!request.local) target=await open(request.target);
    const results=[];
    for(const file of request.files) {
      const before=await stat(source.sftp,file.path);
      if(!before.isFile() || before.isSymbolicLink() || before.size!==file.size) throw Error('Source changed before transfer');
      const dest=request.local ? path.join(request.destination,file.name) : path.posix.join(request.destination,file.name);
      const parts=file.name.split('/').slice(0,-1); let parent=request.destination;
      for(const part of parts) {
        parent=request.local?path.join(parent,part):path.posix.join(parent,part);
        if(request.local) { fs.mkdirSync(parent,{recursive:true}); if(fs.lstatSync(parent).isSymbolicLink())throw Error('Symlink destination'); }
        else await mkdir(target.sftp,parent);
      }
      let size=0; const hash=createHash('sha256');
      const checksum=new Transform({transform(chunk,encoding,cb){size+=chunk.length;hash.update(chunk);cb(null,chunk);}});
      const output=request.local?fs.createWriteStream(dest,{flags:'wx',mode:0o600}):target.sftp.createWriteStream(dest,{flags:'wx',mode:0o600});
      const input=source.sftp.createReadStream(file.path);
      // Keep a listener during SSH teardown, after pipeline has already reported a failure.
      input.on('error',()=>{});output.on('error',()=>{});
      await pipeline(input,checksum,output);
      const after=await stat(source.sftp,file.path);
      if(size!==file.size || before.size!==after.size || before.mtime!==after.mtime)throw Error('Source changed during transfer; destination not registered');
      const sha256=hash.digest('hex');
      // Verify against a second source pass, including same-size/same-second modifications.
      const verify=createHash('sha256');
      const reread=source.sftp.createReadStream(file.path);reread.on('error',()=>{});
      await pipeline(reread,new Writable({write(chunk,encoding,done){verify.update(chunk);done();}}));
      if(sha256!==verify.digest('hex'))throw Error('Source checksum changed; destination not registered');
      results.push({name:file.name,size,sha256});
    }
    return {files:results};
  } finally { source?.connection.end(); target?.connection.end(); }
}
if(require.main===module){
  let input='';process.stdin.setEncoding('utf8');
  process.stdin.on('data',chunk=>{input+=chunk;if(input.length>2*1024*1024)process.exit(1);});
  process.stdin.on('end',()=>{Promise.resolve().then(()=>relay(JSON.parse(input))).then(result=>process.stdout.write(JSON.stringify(result)),error=>process.stdout.write(JSON.stringify({error:error.message})));});
}
module.exports={safeName,relay};
