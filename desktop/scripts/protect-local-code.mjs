// Packaging protection only: the native application must still hold the runtime key.
import {createCipheriv,randomBytes} from 'node:crypto';
import {cpSync,existsSync,mkdirSync,readFileSync,readdirSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

function encrypt(source,key){
  const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);
  const encrypted=Buffer.concat([cipher.update(source),cipher.final()]);
  return Buffer.concat([iv,cipher.getAuthTag(),encrypted]).toString('base64');
}

export function protect(source,key){
  const payload=encrypt(source,key);
  return `// OMaaX protected local module. Original notices are retained in the encrypted source.\n'use strict';\nconst crypto=require('node:crypto');\nconst payload=Buffer.from('${payload}','base64');\nlet source;\ntry {\n const key=Buffer.from(process.env.OMAAX_LOCAL_CODE_KEY||'','hex');\n const decipher=crypto.createDecipheriv('aes-256-gcm',key,payload.subarray(0,12));\n decipher.setAuthTag(payload.subarray(12,28));\n source=Buffer.concat([decipher.update(payload.subarray(28)),decipher.final()]).toString('utf8');\n} catch { throw new Error('OMaaX local component could not load. Please restart or reinstall the application.'); }\nmodule._compile(source,__filename);\n`;
}

// The bundled Node supports synchronous load hooks. Loading the source under
// the same file URL with a private query preserves imports and import.meta.url
// without writing a decrypted file into the installation or user directory.
export function protectEsm(source,key){
  const text=source.toString('utf8');
  const declarations=[...text.matchAll(/^\s*export\s+(?:(?:async\s+)?function|const|class)\s+([A-Za-z_$][\w$]*)/gm)];
  if(declarations.length!==[...text.matchAll(/^\s*export\b/gm)].length)throw Error('Protected ESM requires named const, function, or class exports');
  const exports=declarations.map(match=>match[1]);
  if(new Set(exports).size!==exports.length)throw Error('Duplicate protected ESM export');
  return `// OMaaX protected local module. Original notices are retained in the encrypted source.\nimport {createDecipheriv as _decipher} from 'node:crypto';\nimport {registerHooks as _register} from 'node:module';\nconst _payload=Buffer.from('${encrypt(source,key)}','base64');\nlet _source;\ntry {\n const _key=Buffer.from(process.env.OMAAX_LOCAL_CODE_KEY||'','hex');\n const _cipher=_decipher('aes-256-gcm',_key,_payload.subarray(0,12));\n _cipher.setAuthTag(_payload.subarray(12,28));\n _source=Buffer.concat([_cipher.update(_payload.subarray(28)),_cipher.final()]).toString('utf8');\n} catch { throw new Error('OMaaX local component could not load. Please restart or reinstall the application.'); }\nconst _sourceUrl=new URL(import.meta.url);\n_sourceUrl.searchParams.set('__omaax_protected','1');\nconst _hook=_register({load(url,context,next){\n if(url===_sourceUrl.href)return {format:'module',source:_source,shortCircuit:true};\n return next(url,context);\n}});\nlet _module;\ntry { _module=await import(_sourceUrl.href); } finally { _hook.deregister(); _source=undefined; }\n${exports.map(name=>`export const ${name}=_module.${name};`).join('\n')}\n`;
}

export function stage(source,destination,keyFile,rustFile){
  mkdirSync(destination,{recursive:true});
  const key=existsSync(keyFile)?Buffer.from(readFileSync(keyFile,'utf8').trim(),'hex'):randomBytes(32);
  if(key.length!==32)throw Error('Invalid local protection key');
  mkdirSync(path.dirname(keyFile),{recursive:true});
  writeFileSync(keyFile,key.toString('hex'),{mode:0o600});
  let count=0;
  for(const entry of readdirSync(source,{withFileTypes:true})){
    if(entry.name.includes('.test.')||entry.name.endsWith('_test.py')||entry.name==='.stage-lock')continue;
    const src=path.join(source,entry.name),dest=path.join(destination,entry.name);
    if(entry.isFile()&&entry.name.endsWith('.cjs')){writeFileSync(dest,protect(readFileSync(src),key));count++;}
    else if(entry.isFile()&&entry.name.endsWith('.mjs')){writeFileSync(dest,protectEsm(readFileSync(src),key));count++;}
    else cpSync(src,dest,{recursive:true,force:true});
  }
  writeFileSync(rustFile,`pub const LOCAL_CODE_KEY: &str = "${key.toString('hex')}";\n`);
  return count;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  const [source,destination,keyFile,rustFile]=process.argv.slice(2);
  if(!rustFile)throw Error('Expected source, destination, key file and Rust output');
  console.log(`Protected ${stage(source,destination,keyFile,rustFile)} local modules`);
}
