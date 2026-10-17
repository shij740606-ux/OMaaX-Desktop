import {test} from 'node:test';
import assert from 'node:assert/strict';
import {post} from '../education/src/api/http.js';
test('education upload timeout and pre-cancel settle instead of leaving UI busy',async()=>{
 const original=globalThis.XMLHttpRequest;let sends=0;
 class Xhr {upload={};open(){}send(){sends++;}abort(){this.onabort?.();}}
 globalThis.XMLHttpRequest=Xhr;
 try{
  const body=new FormData();body.append('file',new Blob(['test']),'test.pdf');
  await assert.rejects(post('/api/review/presubmit',body,{timeout:10,onUploadProgress:()=>{}}),e=>e.code==='TIMEOUT');
  const controller=new AbortController();controller.abort();
  await assert.rejects(post('/api/review/presubmit',body,{signal:controller.signal,onUploadProgress:()=>{}}),e=>e.name==='AbortError');
  assert.equal(sends,1);
 }finally{globalThis.XMLHttpRequest=original;}
});
