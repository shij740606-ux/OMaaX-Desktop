import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const desktop=fileURLToPath(new URL('../',import.meta.url));
const runtime=path.resolve(process.env.OMAAX_TEST_DSH_RUNTIME||path.join(desktop,'dsh-runtime'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

test('0.2 settings use live RPC, import a newly written legacy file on every boot, and preserve the managed model route', {timeout:120000}, async()=>{
  const base=path.join(desktop,'../validation-local/settings-sdk');fs.mkdirSync(base,{recursive:true});
  const home=fs.mkdtempSync(path.join(base,'home-'));
  const settings=path.join(home,'settings.yaml');
  const model={api:'openai-completions',baseURL:'http://127.0.0.1:2/current',apiKeyEnv:'OMAAX_SETTINGS_TEST_KEY',models:[{id:'omaax',name:'OMaaX'}]};
  fs.writeFileSync(path.join(home,'cordis.patch.yml'),`- id: llm-deepseek\n  disabled: true\n- id: llm-pi-ai\n  config:\n    providers:\n      deepseek-official: ${JSON.stringify(model)}\n`);
  const legacy=(theme,language)=>`ui-theme:\n  preference: ${theme}\nlocale:\n  preference: ${language}\nllm-pi-ai:\n  providers:\n    deepseek-official: ${JSON.stringify({...model,baseURL:'http://127.0.0.1:1/stale'})}\n`;
  fs.writeFileSync(settings,legacy('light','zh'));
  let child,log='',sequence=0;
  async function stop(){
    if(child){const current=child;child=undefined;if(current.exitCode===null){const ended=new Promise(resolve=>current.once('exit',resolve));current.kill();await ended;}}
    fs.writeFileSync(path.join(home,`boot-${sequence}.log`),log.replace(/([?&]token=)[A-Za-z0-9._-]+/g,'$1[redacted]'));
  }
  async function start(){
    sequence++;log='';
    child=spawn(process.execPath,['--expose-internals',path.join(runtime,'node_modules/@deepseek-ai/dsh/lib/bin.js'),'web','--port','0','--no-open'],{
      cwd:home,windowsHide:true,env:{...process.env,DSH_HOME:home,DSH_AGENTS_HOME:path.join(home,'agents'),DSH_TELEMETRY_DISABLED:'1',DEEPSEEK_API_KEY:'',OPENAI_API_KEY:'',OMAAX_SETTINGS_TEST_KEY:'synthetic'},
    });
    child.stdout.on('data',b=>log+=b);child.stderr.on('data',b=>log+=b);
    const end=Date.now()+40000;
    while(!log.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/)&&Date.now()<end&&child.exitCode===null)await pause(50);
    const url=log.match(/http:\/\/127\.0\.0\.1:\d+\/\?token=[A-Za-z0-9._-]+/)?.[0];assert.ok(url,`Runtime did not start; inspect ${home}`);
    const auth=await fetch(url,{redirect:'manual',signal:AbortSignal.timeout(10000)});
    const cookie=auth.headers.getSetCookie().map(value=>value.split(';')[0]).find(value=>value.startsWith('dsh-auth-'));assert.ok(cookie);
    const rpc=async(method,args={})=>{
      const response=await fetch(`${new URL(url).origin}/api/${method}`,{method:'POST',headers:{'Content-Type':'application/json',Cookie:cookie},body:JSON.stringify({type:'client-request',rpcId:randomUUID(),method,payload:{args}}),signal:AbortSignal.timeout(15000)});
      assert.equal(response.status,200);return (await response.json()).result;
    };
    const read=async()=>{const response=await rpc('settings/describe');assert.equal(response.ok,true,JSON.stringify(response.error));return Object.fromEntries(response.value.namespaces.filter(row=>['ui-theme','locale','llm-pi-ai'].includes(row.ns)).map(row=>[row.ns,row.value]));};
    return {rpc,read};
  }
  async function waitPreferences(read,theme,language){
    const end=Date.now()+10000;let actual;
    do {actual=await read();if(actual['ui-theme']?.preference===theme&&actual.locale?.preference===language)break;await pause(100);}while(Date.now()<end);
    assert.equal(actual['ui-theme'].preference,theme);assert.equal(actual.locale.preference,language);return actual;
  }
  const report={runtime:JSON.parse(fs.readFileSync(path.join(runtime,'node_modules/@deepseek-ai/dsh/package.json'))).version,checks:[]};
  try {
    let host=await start();
    let snapshot=await waitPreferences(host.read,'light','zh');
    assert.ok(fs.existsSync(settings+'.imported'));assert.equal(fs.existsSync(settings),false);
    assert.equal(snapshot['llm-pi-ai'].providers['deepseek-official'].baseURL,model.baseURL);
    report.checks.push('initial legacy UI preferences imported; managed model route retained');

    fs.writeFileSync(settings,legacy('dark','en'));
    await pause(1200);snapshot=await host.read();
    assert.equal(snapshot['ui-theme'].preference,'light');assert.equal(snapshot.locale.preference,'zh');
    report.checks.push('rewriting settings.yaml while running does not update the live UI');
    for(const [ns,preference] of [['ui-theme','dark'],['locale','en']]){
      const response=await host.rpc('settings/update',{ns,patch:{preference}});
      assert.equal(response.ok,true,JSON.stringify(response.error));assert.equal(response.value.value.preference,preference);
    }
    await waitPreferences(host.read,'dark','en');
    report.checks.push('HTTP settings/update direct args apply theme and language immediately');
    const rejected=await host.rpc('settings/update',{ns:'llm-pi-ai',patch:{providers:{'deepseek-official':{...model,baseURL:'http://127.0.0.1:1/stale'}}}});
    assert.equal(rejected.ok,false);assert.match(JSON.stringify(rejected.error),/overridden by a home patch or command-line overlay/);
    report.checks.push('old model settings cannot override the managed home patch');

    await stop();fs.writeFileSync(settings,legacy('system','zh'));
    host=await start();snapshot=await waitPreferences(host.read,'system','zh');
    assert.equal(fs.existsSync(settings),false);assert.match(fs.readFileSync(settings+'.imported','utf8'),/preference: system/);
    assert.equal(snapshot['llm-pi-ai'].providers['deepseek-official'].baseURL,model.baseURL);
    report.checks.push('second boot imports new settings.yaml despite an existing .imported file');
    fs.writeFileSync(path.join(home,'result.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify({home,...report}));
  }finally{await stop();}
});
