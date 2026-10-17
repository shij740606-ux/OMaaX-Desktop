import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createPinia,setActivePinia} from 'pinia';
globalThis.location={pathname:'/test-capability/index.html'};
const listeners=new Set();
globalThis.parent={postMessage(data){queueMicrotask(()=>{for(const fn of listeners)fn({source:parent,data:{type:'omaax-delete-result',requestId:data.requestId,error:'已取消删除'}});});}};
globalThis.window={addEventListener(_,fn){listeners.add(fn);},removeEventListener(_,fn){listeners.delete(fn);}};
const {useEduStore}=await import('../education/src/stores/edu.js');
const {pptDownloadUrl,pptPreviewUrl}=await import('../education/src/api/review.js');
test('cancelled presentation and rebuttal deletion retain jobs and edits',async()=>{
 setActivePinia(createPinia());const store=useEduStore();
 store.ppt.job={id:'ppt_owned',pages:[{title:'Keep my edits'}]};store.ppt.status='editing';
 assert.equal((await store.pptDelete()).ok,false);
 assert.equal(store.ppt.error,'已取消删除');assert.equal(store.ppt.job.pages[0].title,'Keep my edits');assert.equal(store.ppt.status,'editing');
 store.rebuttal.job={id:'rbt_owned'};assert.equal((await store.rebuttalDelete()).ok,false);assert.equal(store.rebuttal.job.id,'rbt_owned');
 assert.equal(listeners.size,0);
});
test('failed presentation save retains the existing job and makes error available',async()=>{
 setActivePinia(createPinia());const store=useEduStore();store.ppt.job={id:'ppt_owned',pages:[{title:'Original'}]};
 const original=globalThis.fetch;globalThis.fetch=async()=>{throw Error('offline');};
 try{
  assert.equal(await store.pptSave([{title:'Edited'}]),null);assert.equal(store.ppt.job.pages[0].title,'Original');assert.equal(store.ppt.saving,false);assert.ok(store.ppt.error);
  globalThis.fetch=async()=>new Response(JSON.stringify({ok:true,job:{id:'ppt_owned',pages:[{title:'Edited'}]}}),{headers:{'content-type':'application/json'}});
  assert.ok(await store.pptSave([{title:'Edited'}]));assert.equal(store.ppt.error,null);assert.equal(store.ppt.job.pages[0].title,'Edited');
 }finally{globalThis.fetch=original;}
});
test('presentation previews and downloads preserve the local access capability',()=>{
 assert.equal(pptDownloadUrl('ppt_owned'),'/test-capability/api/review/ppt/jobs/ppt_owned/pptx');
 assert.equal(pptPreviewUrl('ppt_owned',1),'/test-capability/api/review/ppt/jobs/ppt_owned/preview/1');
});
