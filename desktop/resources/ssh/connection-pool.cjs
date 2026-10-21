const {Client}=require('ssh2');
const {createHash}=require('node:crypto');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

// A native worker serializes requests for one endpoint. Credentials never leave memory.
class ConnectionPool {
  current=null;
  idleTimer=null;
  idle(){clearTimeout(this.idleTimer);this.idleTimer=setTimeout(()=>this.close(),90000);this.idleTimer.unref();}
  close(){clearTimeout(this.idleTimer);const c=this.current;this.current=null;if(c)c.client.destroy();}
  async acquire(request,deadline){
    clearTimeout(this.idleTimer);
    const identity=JSON.stringify([request.host,request.port,request.username,request.hostKey,request.password,request.privateKey,request.passphrase]);
    if(this.current?.identity===identity&&this.current.ready)return this.current;
    this.close();
    for(let attempt=0;attempt<3;attempt++){
      if(attempt)await delay(500*2**(attempt-1));
      const remaining=deadline-Date.now();if(remaining<=0)throw Error('SSH 连接超时');
      const record={client:new Client(),identity,ready:false,hostKey:'',fingerprint:''};this.current=record;
      try{
        await new Promise((resolve,reject)=>{
          const client=record.client;let settled=false,verified=false;
          const fail=error=>{record.ready=false;if(this.current===record)this.current=null;if(!settled){settled=true;reject(error);}};
          client.on('error',fail);
          client.on('close',()=>fail(Error('SSH 连接在握手完成前关闭')));
          client.once('ready',()=>{settled=true;record.ready=true;resolve();});
          client.connect({host:request.host,port:request.port,username:request.username,
            password:request.operation==='probe'?undefined:request.password,
            privateKey:request.operation==='probe'?undefined:request.privateKey,passphrase:request.passphrase,
            readyTimeout:Math.min(30000,remaining),keepaliveInterval:15000,keepaliveCountMax:4,
            hostVerifier:key=>{
              record.hostKey=key.toString('base64');record.fingerprint='SHA256:'+createHash('sha256').update(key).digest('base64').replace(/=+$/,'');
              if(request.operation==='probe'){settled=true;resolve();return false;}
              verified=record.hostKey===request.hostKey;
              if(!verified){const e=Error('SSH 主机指纹不匹配，请核对服务器身份');e.noRetry=true;fail(e);}
              return verified;
            }});
        });
        return record;
      }catch(error){
        record.client.destroy();if(this.current===record)this.current=null;
        if(error.level==='client-authentication')throw Error('SSH 认证失败，请检查用户名、密码或私钥');
        if(error.noRetry||attempt===2||deadline-Date.now()<1000)throw error;
      }
    }
  }
}
module.exports={ConnectionPool};
