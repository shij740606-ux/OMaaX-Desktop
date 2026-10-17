const {test}=require('node:test');const assert=require('node:assert/strict');
const http=require('node:http'),net=require('node:net'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {start,validApi}=require('./education.cjs');
test('education proxy limits routes, origin, deletion and preserves uploads/downloads',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'omaax-edu-'));fs.writeFileSync(path.join(root,'index.html'),'<h1>Education</h1>');
 const seen=[];const upstream=http.createServer((req,res)=>{let body='';req.on('data',b=>body+=b);req.on('end',()=>{seen.push({method:req.method,path:req.url,body});res.setHeader('content-type','application/json');res.end(JSON.stringify({ok:true,pdf_url:'/api/review/compile/abc/pdf',body}));});});
 await new Promise(r=>upstream.listen(0,'127.0.0.1',r));
 const {server,url}=await start({directory:root,token:'test-capability',control:'native-control',forward:cb=>{const s=net.connect(upstream.address().port,'127.0.0.1',()=>{s.setTimeout=undefined;cb(null,s);});s.on('error',e=>cb(e));}});
 const origin=new URL(url).origin,base=origin+'/test-capability';
 try{
  assert.equal((await fetch(url,{headers:{'Sec-Fetch-Site':'cross-site'}})).status,200);assert.equal((await fetch(origin+'/index.html')).status,403);
  assert.equal((await fetch(base+'/api/auth/status')).status,403);
  assert.equal((await fetch(base+'/api/review/journals',{headers:{Origin:'https://attacker.invalid'}})).status,403);
  assert.equal((await fetch(base+'/api/review/ppt/jobs/abc',{method:'DELETE'})).status,403);assert.equal(seen.length,0);
  const body=await (await fetch(base+'/api/review/recommend',{method:'POST',body:'sample-input'})).json();
  assert.equal(body.body,'sample-input');assert.equal(body.pdf_url,'/test-capability/api/review/compile/abc/pdf');
  assert.equal((await fetch(origin+'/control/delete?path=/api/review/ppt/jobs/abc',{method:'POST'})).status,403);
  assert.equal((await fetch(origin+'/control/delete?path=/api/review/ppt/jobs/abc',{method:'POST',headers:{'x-omaax-control':'native-control'}})).status,200);
  assert.equal(seen.at(-1).method,'DELETE');assert.equal(seen.at(-1).path,'/api/review/ppt/jobs/abc');
  for(const target of ['/api/review/../auth','/api/review/ppt/%2e%2e/secret','/api/review/ppt/%0a','http://elsewhere/','/api/review/ppt/%5csecret'])assert.equal(validApi(target),false,target);
 }finally{server.closeAllConnections();server.close();upstream.closeAllConnections();upstream.close();fs.rmSync(root,{recursive:true});}
});
