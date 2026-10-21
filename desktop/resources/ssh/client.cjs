const {StringDecoder}=require('node:string_decoder');
const {ConnectionPool}=require('./connection-pool.cjs');
const pool=new ConnectionPool();
async function run(request){
  if(!['probe','test','execute','files'].includes(request.operation))return {error:'不支持的 SSH 操作'};
  if(request.operation==='files'&&(request.path!==undefined&&(typeof request.path!=='string'||request.path.length>4096||request.path.includes('\0'))||request.offset!==undefined&&(!Number.isInteger(request.offset)||request.offset<0||request.offset>1000000)||request.limit!==undefined&&(!Number.isInteger(request.limit)||request.limit<1||request.limit>200)||request.showHidden!==undefined&&typeof request.showHidden!=='boolean'))return {error:'目录查询参数不正确'};
  if(request.operation==='execute'&&(typeof request.command!=='string'||Buffer.byteLength(request.command)>6*1048576||request.command.includes('\0')))return {error:'SSH 执行内容无效或超过限制'};
  const deadline=Date.now()+Math.min(900,Math.max(5,request.timeoutSeconds||30))*1000;
  let record;
  try{record=await pool.acquire(request,deadline);}catch(error){return {error:error.message,phase:'连接服务器'};}
  if(request.operation!=='execute'&&request.operation!=='files')return {fingerprint:record.fingerprint,hostKey:record.hostKey,connected:request.operation==='test'};
  // Once exec/SFTP may have reached the server, never replay automatically.
  return new Promise(resolve=>{
    const connection=record.client;let finished=false;
    const done=value=>{if(finished)return;finished=true;clearTimeout(timer);connection.removeListener('error',onError);connection.removeListener('close',onClose);if(value.error)pool.close();resolve(value);};
    const onError=error=>done({error:error.message,phase:'执行操作'});
    const onClose=()=>done({error:'SSH 连接意外断开；远端任务可能已提交，请先核对任务记录，不要重复提交',phase:'执行操作'});
    const timer=setTimeout(()=>done({error:'SSH 操作超时；远程命令是否停止需另行确认',phase:'执行操作'}),Math.max(1,deadline-Date.now()));
    connection.once('error',onError);connection.once('close',onClose);
    if(!record.ready){onClose();return;}
    if(request.operation==='files'){require('./browse.cjs')(connection,request).then(done,e=>done({error:e.code===3?'当前账号无权读取该目录':e.code===2?'目录不存在，请检查路径':String(e.message||e),phase:'读取目录'}));return;}
    const command=request.command,streamed=Buffer.byteLength(command)>65536;
    connection.exec(streamed?'/bin/bash -s':command,(error,stream)=>{
      if(finished){stream?.destroy();return;}
      if(error){done({error:error.message});return;}
      let stdout='',stderr='',total=0;const out=new StringDecoder('utf8'),err=new StringDecoder('utf8');
      const collect=(chunk,isError)=>{total+=chunk.length;if(total>1048576){done({error:'输出超过 1 MB，请将结果写入服务器文件后分段查看'});return;}if(isError)stderr+=err.write(chunk);else stdout+=out.write(chunk);};
      stream.on('data',chunk=>collect(chunk,false));stream.stderr.on('data',chunk=>collect(chunk,true));
      stream.on('error',error=>done({error:error.message}));
      stream.on('close',(code,signal)=>done({stdout:stdout+out.end(),stderr:stderr+err.end(),exitCode:code??null,signal:signal??null}));
      if(streamed)stream.end(command+'\n');
    });
  });
}
function parse(input){if(Buffer.byteLength(input)>8*1048576)throw Error('SSH 请求超过 8 MB');return JSON.parse(input);}
if(process.argv.includes('--persistent')){
  let queued=Promise.resolve();
  const lines=require('node:readline').createInterface({input:process.stdin,crlfDelay:Infinity});
  lines.on('line',line=>{queued=queued.then(async()=>{let value;try{value=await run(parse(line));}catch(e){value={error:e.message};}pool.idle();process.stdout.write(JSON.stringify(value)+'\n');});});
  lines.on('close',()=>{queued.finally(()=>{pool.close();process.exit();});});
}else{
  let input='';process.stdin.setEncoding('utf8');
  process.stdin.on('data',chunk=>{input+=chunk;if(Buffer.byteLength(input)>8*1048576){process.stdout.write(JSON.stringify({error:'SSH 请求超过 8 MB'}));process.exit(1);}});
  process.stdin.on('end',async()=>{let value;try{value=await run(parse(input));}catch(e){value={error:e.message};}pool.close();process.stdout.write(JSON.stringify(value));});
}
