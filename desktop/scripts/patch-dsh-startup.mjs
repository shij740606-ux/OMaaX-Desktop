// Patch the standalone boot shell: plugin failure can happen before the normal UI exists.
import {existsSync,readFileSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const marker='/*OMAAX_DSH_STARTUP_V1*/';

function once(source,before,after){
  if(source.split(before).length!==2)throw Error(`Startup patch anchor changed: ${before.slice(0,100)}`);
  return source.replace(before,after);
}

export function patchStartupSource(source){
  if(source.includes(marker)){
    for(const text of ['.wordmark,"OMaaX"','.hint,"正在启动智能体…"','"智能体启动未完成"','"重新加载"','window.location.reload()']){
      if(!source.includes(text))throw Error('Startup patch generated region changed');
    }
    if(source.split(marker).length!==2)throw Error('Startup patch marker is duplicated');
    return source;
  }
  const matches=[...source.matchAll(/this\.wordmark=([\w$]+)\(([\w$]+)\.wordmark,"HARNESS"\)/g)];
  if(matches.length!==1)throw Error('Startup patch boot wordmark anchor changed');
  const [,element,styles]=matches[0];
  const original=`render(){const t=[...this.states].filter(([,i])=>i==="failed").map(([i])=>i);if(this.failure===void 0&&t.length===0){this.spinner.parentElement!==this.card&&this.card.replaceChildren(this.wordmark,this.spinner,this.hint);return}const r=${element}(${styles}.failed);r.append(${element}(${styles}.failedTitle,"Failed to load plugins"));for(const i of t)r.append(${element}(${styles}.failedItem,i));this.failure!==void 0&&r.append(${element}(${styles}.failedItem,this.failure)),this.card.replaceChildren(this.wordmark,r)}`;
  const replacement=`render(){${marker}
    const failed=[...this.states].filter(([,state])=>state==="failed").map(([id])=>id);
    if(this.failure===void 0&&failed.length===0){
      this.spinner.parentElement!==this.card&&this.card.replaceChildren(this.wordmark,this.spinner,this.hint);return;
    }
    const diagnostic=failed.join("\\n")+"\\n"+String(this.failure??"");
    if(this.__omaaxLastFailure!==diagnostic){
      this.__omaaxLastFailure=diagnostic;
      console.error("[OMaaX] Startup failed",{entries:failed,error:this.failure});
    }
    const panel=${element}(${styles}.failed);
    panel.append(${element}(${styles}.failedTitle,"智能体启动未完成"));
    panel.append(${element}(${styles}.failedItem,"本地对话组件未能完成加载。请先重新加载；如果仍然失败，请返回主界面点击“重启智能体”后再试。"));
    const retry=document.createElement("button");retry.type="button";retry.textContent="重新加载";
    retry.style.cssText="margin-top:20px;padding:9px 18px;border:0;border-radius:8px;background:#1677ff;color:#fff;font:inherit;cursor:pointer";
    retry.onclick=()=>{retry.disabled=true;retry.textContent="正在重新加载…";window.location.reload();};
    this.card.replaceChildren(this.wordmark,panel,retry);
  }`;
  source=once(source,original,replacement);
  source=once(source,`.wordmark,"HARNESS"`,`.wordmark,"OMaaX"`);
  source=once(source,`.hint,"Loading plugins…"`,`.hint,"正在启动智能体…"`);
  return source;
}

// Retry only a script transport error. Never replay a script that loaded or ran a factory.
export function patchBundleLoader(source){
  const marker='/*OMAAX_BUNDLE_RETRY_V1*/';
  if(source.includes(marker))return source;
  const start=source.indexOf('const defaultLoadBundle = (url) => new Promise('),end=source.indexOf('\n\t\t/** Replace the rev query',start);
  if(start<0||end<0||!source.slice(start,end).includes('document.head.append(el);'))throw Error('Bundle loader patch anchor changed');
  const patched=`const defaultLoadBundle = (url) => new Promise((resolve, reject) => {
            ${marker}
            const attempt = (remaining) => {
                const el = document.createElement("script");
                el.async = true;el.src = url;
                el.addEventListener("load", () => {el.remove();resolve();}, {once:true});
                el.addEventListener("error", () => {
                    el.remove();
                    if(remaining) {setTimeout(() => attempt(remaining-1),300);return;}
                    reject(new Error(\`client-modules: bundle script \${url} failed to load\`));
                }, {once:true});
                document.head.append(el);
            };
            attempt(1);
        });`;
  return source.slice(0,start)+patched+source.slice(end);
}

export function patchLayoutTitle(source){
  const original='const productTitle = "DeepSeek Harness";';
  const replacement='/*OMAAX_WINDOW_TITLE_V1*/ const productTitle = "OMaaX";';
  if(source.includes('/*OMAAX_WINDOW_TITLE_V1*/')){
    if(source.split(replacement).length!==2||source.includes(original))throw Error('Window title generated region changed');
    return source;
  }
  return once(source,original,replacement);
}

export function verifyDshStartup(runtimeDir=path.join(root,'dsh-runtime')){
  const dist=path.join(runtimeDir,'node_modules/@deepseek-ai/dsh-web-frontend/dist');
  const html=readFileSync(path.join(dist,'index.html'),'utf8');
  if(!html.includes('<title>OMaaX</title>'))throw Error('OMaaX startup title missing');
  const src=[...html.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)][0]?.[1];
  if(!src)throw Error('Startup frontend entry missing');
  const code=readFileSync(path.resolve(dist,src),'utf8');
  if(!code.includes(marker)||patchStartupSource(code)!==code)throw Error('OMaaX startup UI patch missing');
  const loaders=['node_modules/@deepseek-ai/dsh-client-modules/lib/client.js','node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-modules/lib/client.js'].map(f=>path.join(runtimeDir,f)).filter(existsSync);
  if(!loaders.length||loaders.some(f=>!readFileSync(f,'utf8').includes('/*OMAAX_BUNDLE_RETRY_V1*/')))throw Error('Startup resource retry missing');
  const layout=readFileSync(path.join(runtimeDir,'node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-ui-layout/lib/client.js'),'utf8');
  if(!layout.includes('/*OMAAX_WINDOW_TITLE_V1*/')||patchLayoutTitle(layout)!==layout)throw Error('Runtime window title missing');
}

export function patchDshStartup(runtimeDir=path.join(root,'dsh-runtime')){
  const bases=['node_modules/@deepseek-ai/dsh-web-frontend/dist','node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-web-frontend/dist']
    .map(relative=>path.resolve(runtimeDir,relative)).filter(dir=>existsSync(path.join(dir,'index.html')));
  if(!bases.length)throw Error('Startup frontend runtime missing');
  const updates=[];
  for(const dist of bases){
    const index=path.join(dist,'index.html'),html=readFileSync(index,'utf8');
    const scripts=[...html.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)].map(match=>match[1]);
    const entries=[];
    for(const src of scripts){
      if(/^(?:[a-z]+:|\/\/)/i.test(src))continue;
      const file=path.resolve(dist,src.split(/[?#]/)[0].replace(/^\//,''));
      if(!file.startsWith(dist+path.sep)||!existsSync(file))throw Error('Startup frontend entry is missing or outside its distribution');
      const text=readFileSync(file,'utf8');
      if(text.includes('dataset.dshBoot=')||text.includes(marker))entries.push({file,text});
    }
    if(entries.length!==1)throw Error('Startup frontend entry anchor changed');
    updates.push([entries[0].file,patchStartupSource(entries[0].text)]);
    const titles=[...html.matchAll(/<title\b[^>]*>[\s\S]*?<\/title>/gi)];
    if(titles.length!==1)throw Error('Startup frontend title anchor changed');
    updates.push([index,html.replace(titles[0][0],'<title>OMaaX</title>')]);
    const manifest=path.join(dist,'manifest.webmanifest');
    if(existsSync(manifest)){
      const value=JSON.parse(readFileSync(manifest,'utf8'));
      value.name='OMaaX';value.short_name='OMaaX';
      updates.push([manifest,JSON.stringify(value,null,2)+'\n']);
    }
  }
  const loaders=['node_modules/@deepseek-ai/dsh-client-modules/lib/client.js','node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-modules/lib/client.js'].map(f=>path.resolve(runtimeDir,f)).filter(existsSync);
  if(!loaders.length)throw Error('Startup bundle loader missing');
  for(const file of loaders)updates.push([file,patchBundleLoader(readFileSync(file,'utf8'))]);
  const layouts=['node_modules/@deepseek-ai/dsh-client-ui-layout/lib/client.js','node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-client-ui-layout/lib/client.js'].map(f=>path.resolve(runtimeDir,f)).filter(existsSync);
  if(!layouts.length)throw Error('Window title runtime missing');
  for(const file of layouts)updates.push([file,patchLayoutTitle(readFileSync(file,'utf8'))]);
  // Validate every installed layout before touching any runtime file.
  for(const [file,text] of updates)writeFileSync(file,text);
  return updates.map(([file])=>file);
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){
  console.log(`OMaaX startup UI ready (${patchDshStartup(process.argv[2]).length} files)`);
}
