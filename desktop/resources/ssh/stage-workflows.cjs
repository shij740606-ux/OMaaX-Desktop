// Only the application's immutable workflow archive enters this authenticated upload.
const {Client}=require('ssh2');
const {createHash,randomUUID}=require('node:crypto');
const {Readable}=require('node:stream');
const {pipeline}=require('node:stream/promises');
let input='';process.stdin.setEncoding('utf8');
process.stdin.on('data',b=>{input+=b;if(input.length>48*1024*1024)process.exit(1);});
process.stdin.on('end',()=>run(JSON.parse(input)).then(v=>process.stdout.write(JSON.stringify(v)),e=>process.stdout.write(JSON.stringify({error:e.message}))));
async function run(request){
 const bytes=Buffer.from(request.bundle,'base64');
 if(bytes.length>32*1024*1024||createHash('sha256').update(bytes).digest('hex')!==request.sha256)throw Error('Workflow source checksum mismatch');
 if(typeof request.destination!=='string'||!/^\/[^\r\n\0]+\/workflow-code\/[a-f0-9]{64}\/kernel\.zip$/.test(request.destination)||request.destination.split('/').includes('..'))throw Error('Invalid workflow destination');
 const conn=new Client();const timer=setTimeout(()=>conn.destroy(),120000);
 try{
  const sftp=await new Promise((resolve,reject)=>{conn.on('error',reject);conn.on('ready',()=>conn.sftp((e,s)=>e?reject(e):resolve(s)));conn.connect({...request.connection,readyTimeout:15000,hostVerifier:k=>k.toString('base64')===request.connection.hostKey});});
  const parent=request.destination.slice(0,request.destination.lastIndexOf('/'));
  const info=await new Promise((resolve,reject)=>sftp.lstat(parent,(e,s)=>e?reject(e):resolve(s)));
  if(!info.isDirectory()||info.isSymbolicLink())throw Error('Invalid workflow source directory');
  const temp=request.destination+'.'+randomUUID()+'.tmp';const stream=sftp.createWriteStream(temp,{flags:'wx',mode:0o600});stream.on('error',()=>{});
  await pipeline(Readable.from([bytes]),stream);
  await new Promise((resolve,reject)=>sftp.rename(temp,request.destination,e=>e?reject(e):resolve()));
  return {ready:true,size:bytes.length,sha256:request.sha256};
 }finally{clearTimeout(timer);conn.end();}
}
