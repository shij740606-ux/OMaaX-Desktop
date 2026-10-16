const assert=require('node:assert/strict');
const {generateKeyPairSync}=require('node:crypto');
const {spawn}=require('node:child_process');
const {Server,utils}=require('ssh2');
const posix=require('node:path').posix;
const key=generateKeyPairSync('rsa',{modulusLength:2048}).privateKey.export({type:'pkcs1',format:'pem'});
const status=utils.sftp.STATUS_CODE,handles=new Map();let authentication=0,executions=0,mutations=0,serial=0;
const attrs=(directory=false)=>({mode:directory?0o40755:0o100644,uid:1000,gid:1000,size:directory?4096:123,atime:1700000000,mtime:1700000000});
const home=[{filename:'folder space',longname:'folder',attrs:attrs(true)},{filename:'样本 $() ;.txt',longname:'literal filename',attrs:attrs()},{filename:'.hidden',longname:'hidden',attrs:attrs()},...Array.from({length:430},(_,i)=>({filename:'sample-'+i+'.csv',longname:'sample',attrs:attrs()}))];
const server=new Server({hostKeys:[key]},client=>{
 client.on('error',()=>{});
 client.on('authentication',ctx=>{authentication++;ctx.username==='test'&&ctx.method==='password'&&ctx.password==='test'?ctx.accept():ctx.reject();});
 client.on('ready',()=>client.on('session',accept=>{
  const session=accept();session.on('exec',accept=>{executions++;const stream=accept();stream.exit(1);stream.end();});
  session.on('sftp',accept=>{
   const stream=accept();
   stream.on('REALPATH',(id,path)=>{const name=path==='.'?'/home/test':posix.normalize(path);stream.name(id,[{filename:name,longname:name,attrs:attrs(true)}]);});
   stream.on('STAT',(id,path)=>{if(path==='/denied')stream.status(id,status.PERMISSION_DENIED);else if(path==='/missing')stream.status(id,status.NO_SUCH_FILE);else stream.attrs(id,attrs(path!=='/home/test/file.txt'));});
   stream.on('OPENDIR',(id,path)=>{const handle=Buffer.from(String(++serial));handles.set(handle.toString(),{cursor:0,rows:path==='/home/test'?home:[]});stream.handle(id,handle);});
   stream.on('READDIR',(id,handle)=>{const state=handles.get(handle.toString()),rows=state.rows.slice(state.cursor,state.cursor+7);state.cursor+=rows.length;rows.length?stream.name(id,rows):stream.status(id,status.EOF);});
   stream.on('CLOSE',(id,handle)=>{handles.delete(handle.toString());stream.status(id,status.OK);});
   for(const operation of ['WRITE','REMOVE','RENAME','MKDIR','RMDIR','SETSTAT'])stream.on(operation,id=>{mutations++;stream.status(id,status.PERMISSION_DENIED);});
  });
 }));
});
const request=body=>new Promise((resolve,reject)=>{
 const child=spawn(process.execPath,[require('node:path').join(__dirname,'client.cjs')]);let output='';child.stdout.on('data',chunk=>output+=chunk);child.on('error',reject);child.on('close',()=>{try{resolve(JSON.parse(output));}catch(e){reject(e);}});child.stdin.end(JSON.stringify({host:'127.0.0.1',port:server.address().port,username:'test',password:'test',timeoutSeconds:5,...body}));
});
server.listen(0,'127.0.0.1',async()=>{
 try{
  const probe=await request({operation:'probe'}),base={operation:'files',hostKey:probe.hostKey};
  const first=await request({...base,path:'',limit:3});assert.equal(first.home,'/home/test');assert.equal(first.path,'/home/test');assert.equal(first.parent,'/home');assert.equal(first.files.length,3);assert.equal(first.nextOffset,3);assert.equal(first.files[1].path,'/home/test/样本 $() ;.txt');assert.equal(first.files[1].permissions,'644');assert.ok(first.files.every(f=>!f.name.startsWith('.')));
  const next=await request({...base,path:first.path,offset:3,limit:3});assert.ok(next.files.every(f=>!first.files.some(old=>old.path===f.path)));
  const hidden=await request({...base,path:'~',showHidden:true,limit:200});assert.ok(hidden.files.some(f=>f.name==='.hidden'));assert.equal(hidden.nextOffset,200);
  const last=await request({...base,path:'~',offset:400,limit:200});assert.equal(last.nextOffset,null);assert.ok(last.files.length>0);
  const nested=await request({...base,path:'~/folder space'});assert.equal(nested.path,'/home/test/folder space');assert.equal(nested.files.length,0);
  assert.equal((await request({...base,path:'/'})).parent,null);
  assert.match((await request({...base,path:'/denied'})).error,/无权/);assert.match((await request({...base,path:'/missing'})).error,/不存在/);assert.match((await request({...base,path:'/home/test/file.txt'})).error,/不是文件夹/);
  const before=authentication;for(const extra of [{offset:-1},{limit:201},{path:'bad\0path'},{showHidden:'yes'}])assert.ok((await request({...base,...extra})).error);assert.equal(authentication,before);
  assert.ok((await request({...base,hostKey:'wrong'})).error);assert.ok((await request({...base,password:'wrong'})).error);
  assert.equal(executions,0);assert.equal(mutations,0);assert.equal(handles.size,0);
  console.log('SFTP home/absolute/relative/Unicode paths, 430-file paging, hidden files, permissions, authentication, validation and read-only checks passed');
 }catch(e){console.error(e);process.exitCode=1;}finally{server.close();}
});
