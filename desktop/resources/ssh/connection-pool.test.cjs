const {test}=require('node:test');
const assert=require('node:assert/strict');
const {Server,utils}=require('ssh2');
const {generateKeyPairSync}=require('node:crypto');
const {spawn}=require('node:child_process');
const net=require('node:net'),path=require('node:path'),readline=require('node:readline');
const listen=s=>new Promise(resolve=>s.listen(0,'127.0.0.1',resolve));
test('persistent SSH reuses connections, retries handshake only and reconnects after dropped commands',async()=>{
 const key=generateKeyPairSync('rsa',{modulusLength:2048}).privateKey.export({type:'pkcs1',format:'pem'});
 let auths=0,execs=0,drops=0,sockets=new Set();
 const server=new Server({hostKeys:[key]},c=>{
  c.on('error',()=>{});c.on('authentication',ctx=>{if(ctx.method==='password')auths++;ctx.method==='password'&&ctx.password==='test'?ctx.accept():ctx.reject();});
  c.on('ready',()=>c.on('session',accept=>accept().on('exec',(accept,reject,info)=>{execs++;const s=accept();if(info.command==='drop-after-submit'){drops++;c.destroy();return;}setTimeout(()=>{s.write('ok');s.exit(0);s.end();},30);})));});
 await listen(server);
 let connections=0;
 const proxy=net.createServer(socket=>{
  connections++;sockets.add(socket);socket.on('close',()=>sockets.delete(socket));socket.on('error',()=>{});
  if(connections<=2){socket.destroy();return;}
  const upstream=net.connect(server.address().port,'127.0.0.1');sockets.add(upstream);upstream.on('close',()=>sockets.delete(upstream));upstream.on('error',()=>socket.destroy());socket.on('close',()=>upstream.destroy());socket.pipe(upstream).pipe(socket);
 });await listen(proxy);
 const child=spawn(process.execPath,[path.join(__dirname,'client.cjs'),'--persistent']);const waiting=[];
 readline.createInterface({input:child.stdout}).on('line',s=>waiting.shift()?.(JSON.parse(s)));
 const body={host:'127.0.0.1',port:proxy.address().port,username:'test',password:'test',hostKey:utils.parseKey(key).getPublicSSH().toString('base64'),timeoutSeconds:10,operation:'execute',command:'hello'};
 const request=extra=>new Promise(resolve=>{waiting.push(resolve);child.stdin.write(JSON.stringify({...body,...extra})+'\n');});
 try{
  const replies=await Promise.all(Array.from({length:20},()=>request({})));
  assert.ok(replies.every(r=>r.stdout==='ok'));assert.equal(connections,3);assert.equal(auths,1);assert.equal(execs,20);
  assert.match((await request({command:'drop-after-submit'})).error,/断开/);assert.equal(drops,1);
  assert.equal((await request({})).stdout,'ok');assert.equal(connections,4);assert.equal(drops,1);
  const before=execs;assert.match((await request({hostKey:'wrong'})).error,/指纹/);assert.equal(execs,before);assert.equal(connections,5);
  assert.match((await request({password:'wrong'})).error,/认证失败/);assert.equal(connections,6);
  assert.equal((await request({})).stdout,'ok');
 }finally{child.kill();for(const socket of sockets)socket.destroy();proxy.close();server.close();}
});

test('slow authentication beyond the former 15 second limit succeeds',async()=>{
 const {ConnectionPool}=require('./connection-pool.cjs');
 const key=generateKeyPairSync('rsa',{modulusLength:2048}).privateKey.export({type:'pkcs1',format:'pem'});
 const server=new Server({hostKeys:[key]},c=>{c.on('error',()=>{});c.on('authentication',ctx=>{if(ctx.method==='password')setTimeout(()=>ctx.accept(),16000);else ctx.reject();});});
 await listen(server);const pool=new ConnectionPool();
 try{const r=await pool.acquire({host:'127.0.0.1',port:server.address().port,username:'test',password:'test',hostKey:utils.parseKey(key).getPublicSSH().toString('base64'),operation:'test'},Date.now()+25000);assert.equal(r.ready,true);}finally{pool.close();server.close();}
});
