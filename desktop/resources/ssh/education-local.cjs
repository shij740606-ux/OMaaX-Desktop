// Cached local Python environment; model requests use the authenticated company bridge.
const fs=require('node:fs');
const path=require('node:path');
const net=require('node:net');
const {spawn}=require('node:child_process');
const {createHash,randomBytes}=require('node:crypto');
const readline=require('node:readline');

function runtimeEnvironment(state,inherited=process.env){
  const env={...inherited};
  // A new user's setup must not depend on developer Python, uv settings or a proxy.
  for(const name of Object.keys(env))if(/^(http_proxy|https_proxy|all_proxy|node_options|pythonpath|pythonhome|virtual_env)$|^(REVIEW_|DOCGEN_LLM_|UV_|PIP_)/i.test(name))delete env[name];
  return {...env,PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1',PYTHONNOUSERSITE:'1',UV_NO_PROGRESS:'1',UV_HTTP_TIMEOUT:'120',UV_LINK_MODE:'copy',UV_CACHE_DIR:path.join(state,'uv-cache'),UV_PYTHON_INSTALL_DIR:path.join(state,'python')};
}

async function launch(config){
  fs.mkdirSync(config.state,{recursive:true});
  const requirements=path.join(config.service,'requirements.txt');
  const digest=createHash('sha256').update(fs.readFileSync(requirements)).digest('hex');
  const runtime=path.join(config.state,'runtime');
  const python=path.join(runtime,process.platform==='win32'?'Scripts/python.exe':'bin/python');
  const marker=path.join(runtime,'requirements.sha256');
  const env=runtimeEnvironment(config.state);
  const run=args=>new Promise((resolve,reject)=>{
    const child=spawn(config.uv,['--no-config',...args],{env,cwd:config.state,windowsHide:true,stdio:['ignore','ignore','pipe']});
    const stop=()=>child.kill();process.once('exit',stop);
    let tail='';child.stderr.on('data',b=>{tail=(tail+b).slice(-8000);});
    const timer=setTimeout(()=>child.kill(),480000);
    child.on('error',reject);child.on('close',code=>{process.removeListener('exit',stop);clearTimeout(timer);if(code===0)resolve();else{
      fs.writeFileSync(path.join(config.state,'runtime-setup.log'),tail);reject(Error('Local runtime setup failed'));
    }});
  });
  if(!fs.existsSync(python))await run(['venv','--python','3.12.12','--python-preference','only-managed',runtime]);
  if(!fs.existsSync(marker)||fs.readFileSync(marker,'utf8')!==digest){
    await run(['pip','sync','--python',python,'--index-url','https://pypi.org/simple',requirements]);
    fs.writeFileSync(marker,digest);
  }
  const token=randomBytes(32).toString('hex');
  const child=spawn(python,['-u',path.join(config.service,'serve.py')],{env,windowsHide:true,stdio:['pipe','pipe','pipe']});
  // Only the backend receives the temporary loopback key, never a provider key.
  child.stdin.write(JSON.stringify({state:config.state,llm:config.llm,tectonic:config.tectonic,token,windowsRuntime:process.platform==='win32'?path.join(path.dirname(config.service),'documents'):undefined})+'\n');
  child.stderr.on('data',()=>{});
  const port=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{child.kill();reject(Error('Local backend timeout'));},60000);
    const fail=()=>{clearTimeout(timer);reject(Error('Local backend failed'));};
    child.once('error',fail);child.once('exit',fail);
    readline.createInterface({input:child.stdout}).once('line',line=>{
      clearTimeout(timer);try{const {port}=JSON.parse(line);if(!Number.isInteger(port)||port<1||port>65535)throw Error();resolve(port);}catch{child.kill();reject(Error('Invalid backend port'));}
    });
  });
  return {child,token,forward:cb=>{const socket=net.connect(port,'127.0.0.1');socket.once('error',cb);socket.once('connect',()=>{socket.removeListener('error',cb);cb(null,socket);});}};
}
module.exports={launch,runtimeEnvironment};
