// Loopback-only education UI and fixed review-service proxy. SSH secrets never reach the page.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { randomBytes } = require('node:crypto');
const DELETE_PATH = /^\/api\/review\/(?:ppt|rebuttal)\/jobs\/[a-zA-Z0-9_-]{1,80}$/;
const API_PATH = /^\/api\/review\/(?:journals|conferences|recommend|presubmit|format-check|reformat|compile|rebuttal|ppt)(?:[/?][a-zA-Z0-9_./?=&%+,:~-]*)?$/;
function validApi(url) {
  try { return API_PATH.test(url) && !decodeURIComponent(url).split(/[/?]/).includes('..') && !/[\\\r\n\0]/.test(decodeURIComponent(url)); } catch { return false; }
}
function start({ directory, forward, control, token = randomBytes(32).toString('hex') }) {
  const prefix = `/${token}`;
  const root = fs.realpathSync(directory);
  function error(res, status, message) { res.writeHead(status, {'Content-Type':'application/json'}); res.end(JSON.stringify({ok:false,error:{code:'UNAVAILABLE',message}})); }
  function proxy(req, res, url, method) {
    forward((err, socket) => {
      if (err) return error(res,503,'科研服务连接失败，请重试');
      const headers = {};
      for (const key of ['content-type','content-length','accept','range']) if(req.headers[key])headers[key]=req.headers[key];
      if(method==='DELETE') {delete headers['content-length'];delete headers['content-type'];}
      const agent=new http.Agent();agent.createConnection=()=>socket;
      const upstream = http.request({host:'127.0.0.1',port:8100,path:url,method,headers,agent}, reply=>{
        const safe = {};
        for(const key of ['content-type','content-length','content-disposition','content-range','accept-ranges'])if(reply.headers[key])safe[key]=reply.headers[key];
        if((safe['content-type']||'').includes('application/json')) {
          const chunks=[];let size=0;reply.on('data',b=>{size+=b.length;if(size>100*1048576){reply.destroy();res.destroy();}else chunks.push(b);});
          reply.on('end',()=>{delete safe['content-length'];res.writeHead(reply.statusCode,safe);res.end(Buffer.concat(chunks).toString().replaceAll('"/api/review/', '"'+prefix+'/api/review/'));});
        } else {res.writeHead(reply.statusCode,safe);reply.pipe(res);}
      });
      const timeout=setTimeout(()=>upstream.destroy(Error('timeout')),600000);
      upstream.on('close',()=>{clearTimeout(timeout);agent.destroy();});
      upstream.on('error',()=>{if(!res.headersSent)error(res,503,'科研服务连接中断，请重试');else res.destroy();});
      res.on('close',()=>upstream.destroy());
      if(method==='DELETE')upstream.end();else req.pipe(upstream);
    });
  }
  const server = http.createServer((req,res)=>{
    const origin = `http://127.0.0.1:${server.address().port}`;
    res.setHeader('Referrer-Policy','no-referrer');res.setHeader('Cache-Control','no-store');
    res.setHeader('X-Content-Type-Options','nosniff');
    if(req.headers.host!==new URL(origin).host) return error(res,403,'访问地址无效');
    // Separate capability known only to the native process; normal HTTP DELETE is always refused.
    if(req.url.startsWith('/control/delete?') && req.method==='POST' && req.headers['x-omaax-control']===control) {
      const target=new URL(req.url,origin).searchParams.get('path');
      if(!DELETE_PATH.test(target||''))return error(res,403,'删除目标无效');
      return proxy(req,res,target,'DELETE');
    }
    if(!req.url.startsWith(prefix+'/'))return error(res,403,'请从教育科研入口重新打开');
    req.url=req.url.slice(prefix.length);
    if(req.url.startsWith('/api/')) {
    if(req.headers.origin && req.headers.origin!==origin || req.headers['sec-fetch-site']==='cross-site') return error(res,403,'不允许跨站操作');

      if(!['GET','POST','PATCH','PUT'].includes(req.method)||!validApi(req.url))return error(res,403,'此操作需要在软件中确认');
      if(Number(req.headers['content-length']||0)>55*1048576)return error(res,413,'文件过大，请使用不超过50 MB的文件');
      return proxy(req,res,req.url,req.method);
    }
    if(req.method!=='GET')return error(res,405,'不支持此操作');
    let file;
    try {file=fs.realpathSync(path.join(root,decodeURIComponent(req.url.split('?')[0])));}catch{return error(res,404,'页面资源不存在');}
    if(!file.startsWith(root+path.sep)||!fs.statSync(file).isFile())return error(res,403,'路径无效');
    const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml'}[path.extname(file)];
    if(!mime)return error(res,403,'文件类型不可访问');
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'");
    res.writeHead(200,{'Content-Type':mime});fs.createReadStream(file).pipe(res);
  });
  return new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve({server,url:`http://127.0.0.1:${server.address().port}/${token}/index.html`})));
}
module.exports={start,validApi,DELETE_PATH};
if(require.main===module){
  const {Client}=require('ssh2');const readline=require('node:readline');
  const input=readline.createInterface({input:process.stdin});let connection;
  input.once('line',async line=>{
    try{
      const config=JSON.parse(line);connection=new Client();
      connection.on('error',()=>{process.stdout.write(JSON.stringify({error:'科研服务连接失败，请检查服务器连接'})+'\n');process.exit(1);});
      connection.on('close',()=>process.exit(0));
      connection.on('ready',async()=>{
        try { await require('./account-policy.cjs')(connection,config.accountOwner);
        const result=await start({directory:config.directory,control:config.control,forward:cb=>connection.forwardOut('127.0.0.1',0,'127.0.0.1',8100,cb)});
        process.stdout.write(JSON.stringify({url:result.url})+'\n'); } catch {process.stdout.write(JSON.stringify({error:'科研服务连接失败'})+'\n');process.exit(1);}
      });
      connection.connect({host:config.host,port:config.port,username:config.username,password:config.password||undefined,privateKey:config.privateKey||undefined,passphrase:config.passphrase||undefined,readyTimeout:15000,keepaliveInterval:15000,keepaliveCountMax:3,hostVerifier:key=>key.toString('base64')===config.hostKey});
    }catch{process.stdout.write(JSON.stringify({error:'科研服务启动失败'})+'\n');process.exit(1);}
  });
  input.on('close',()=>{connection?.end();process.exit(0);});
}
