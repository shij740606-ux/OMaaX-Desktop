import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,readdir} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {externalPolicy,createGuard} from './index.js';
import {createImageTool} from './image.js';

test('external tools proceed without extra approval but cancellation and login remain effective',async()=>{
 const policy=externalPolicy(['papers'],{request:async()=>{throw Error('Unexpected approval');}});
 const exec={name:'mcp__papers__search'};
 assert.equal((await policy.before(exec,async()=>({kind:'allow'}))).kind,'allow');
 assert.equal(createGuard(()=>({ok:true}))(exec),undefined);
 assert.match(createGuard(()=>({ok:false}))(exec),/账号/);
 assert.equal((await policy.before(exec,async()=>({kind:'deny'}))).kind,'deny');
 const abort=new AbortController();abort.abort();
 assert.equal((await policy.before({...exec,signal:abort.signal},async()=>({kind:'allow'}))).kind,'deny');
});
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhXkAAAAASUVORK5CYII=','base64');
test('image API saves distinct files, renders preview, keeps key out of returned data and rejects overwrite arguments',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'omaax-image-'));
 const env={OMAAX_IMAGE_WORKSPACE:root,BIODSH_IMAGE_BASE_URL:'https://example.test/v1',BIODSH_IMAGE_MODEL:'test',BIODSH_IMAGE_API_KEY:'secret',BIODSH_FILES_URL:'http://127.0.0.1:123/f/token'};
 const request=async(url,opts)=>{assert.equal(String(url),'https://example.test/v1/images/generations');assert.equal(opts.headers.Authorization,'Bearer secret');assert.equal(JSON.parse(opts.body).prompt,'cell');return new Response(JSON.stringify({data:[{b64_json:png.toString('base64')}]}));};
 const tool=createImageTool(env,request),exec={signal:new AbortController().signal};
 const a=await tool.execute({prompt:'cell'},exec),b=await tool.execute({prompt:'cell'},exec);
 assert.notEqual(a.path,b.path);assert.deepEqual(await readFile(a.path),png);assert.match(a.markdown,/http:\/\/127/);assert.ok(!JSON.stringify(a).includes('secret'));assert.equal((await readdir(root)).length,2);
 await assert.rejects(tool.execute({prompt:'cell',path:'existing-file'},exec),/参数/);
 await assert.rejects(createImageTool(env,async()=>new Response('bad',{status:401})).execute({prompt:'cell'},exec),/HTTP 401/);
 await assert.rejects(createImageTool(env,async()=>new Response('{}')).execute({prompt:'cell'},exec),/未返回/);
 await assert.rejects(createImageTool({},request).execute({prompt:'cell'},exec),/配置/);
});
test('URL images are downloaded without API credentials and non-images never saved',async()=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'omaax-image-url-'));
 const env={OMAAX_IMAGE_WORKSPACE:root,BIODSH_IMAGE_BASE_URL:'https://example.test',BIODSH_IMAGE_MODEL:'test',BIODSH_IMAGE_API_KEY:'secret'};
 let count=0;const request=async(url,opts)=>{if(++count===1)return new Response(JSON.stringify({data:[{url:'https://images.test/1.png'}]}));assert.equal(opts.headers,undefined);return new Response(png);};
 await createImageTool(env,request).execute({prompt:'cell'},{signal:new AbortController().signal});
 await assert.rejects(createImageTool(env,async()=>new Response(JSON.stringify({data:[{b64_json:Buffer.from('<html>').toString('base64')}]}))).execute({prompt:'cell'},{signal:new AbortController().signal}),/格式/);
 assert.equal((await readdir(root)).length,1);
});
