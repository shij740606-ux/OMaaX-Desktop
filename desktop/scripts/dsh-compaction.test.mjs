import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL, fileURLToPath} from 'node:url';
import path from 'node:path';
import {patchCompaction} from './patch-dsh-compaction.mjs';

const runtime=process.env.OMAAX_TEST_DSH_RUNTIME||fileURLToPath(new URL('../dsh-runtime',import.meta.url));
test('compaction patch is idempotent',()=>{
  const files=patchCompaction(runtime),before=files.map(f=>readFileSync(f,'utf8'));
  patchCompaction(runtime);assert.deepEqual(files.map(f=>readFileSync(f,'utf8')),before);
});

test('transport compaction partitions oversized history without losing source data',async t=>{
 const require=createRequire(path.join(runtime,'node_modules/@deepseek-ai/dsh/lib/bin.js'));
 const {BasicCompactionEngine}=await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-compaction-basic')).href);
 const {createUserMessage}=await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-llm')).href);
 const agent={session:{id:'fixture',requestHeader:()=>({config:{provider:'fixture',model:'fixture'}})},options:{}};
 const config={summarizationProvider:'',summarizationModel:'',maxTokens:8192,modelPolicies:[]};
 const text='Task ID running-175; original data stays protected. '+('水稻🧬\\\"\n'.repeat(70000))+' End: do not resubmit completed job.';
 const input={messages:[createUserMessage({content:[{type:'text',text}],source:{kind:'user'}})]};
 const chunks=text=>[{type:'text-delta',index:0,text},{type:'usage',usage:{inputTokens:20,outputTokens:4,totalTokens:24}},{type:'finish',reason:{kind:'completed'}}];
 async function run(responder,controller=new AbortController(),fixture=input){
  const calls=[];
  const ctx={llm:{async *stream(options){calls.push(structuredClone({...options,signal:undefined}));assert.ok(Buffer.byteLength(JSON.stringify(options))<1048576);yield* responder(options,calls.length,controller);}}};
  const result=await BasicCompactionEngine.prototype.summarize.call({ctx,config},fixture,agent,controller.signal);
  return {result,calls};
 }
 await t.test('all Unicode, escaping and first/last task details reach the model once as source fragments',async()=>{
  const before=JSON.stringify(input);
  const {result,calls}=await run((o,n)=>chunks(`checkpoint ${n}: task running-175 already submitted, query status before resuming.`));
  const parts=calls.filter(o=>o.messages[0].content[0].text.startsWith('以下是历史对话资料片段')).map(o=>o.messages[0].content[0].text.split('\n').slice(1).join('\n'));
  assert.ok(parts.length>2);assert.equal(parts.join(''),input.messages.map(m=>JSON.stringify(m)).join('\n'));
  assert.ok(parts.every(p=>!/[\uD800-\uDBFF]$/.test(p)));
  assert.equal(result.usage.totalTokens,calls.length*24);
  assert.equal(JSON.stringify(input),before,'original source is never changed');
  assert.match(result.summary[0].text,/running-175/);
 });
 await t.test('cancellation between batches stops without returning a partial checkpoint',async()=>{
  let count=0;
  await assert.rejects(()=>run((o,n,c)=>{count=n;c.abort(new Error('stop batches'));return chunks('partial');}),/stop batches/);
  assert.equal(count,1);
 });
 await t.test('a failed batch aborts the checkpoint transaction',async()=>{
  await assert.rejects(()=>run((o,n)=>n===2?[{type:'finish',reason:{kind:'error',failure:{message:'batch failed',code:'FIXTURE_BATCH'}}}]:chunks('partial')),e=>e.code==='FIXTURE_BATCH');
 });
 await t.test('image content is actually sent to the model and the original image is retained',async()=>{
  const image={type:'image',attachment:{attachmentId:'sha256:fixture',name:'protein.png',bytes:100,mimeType:'image/png'}};
  const fixture=structuredClone(input);fixture.messages[0].content.push({type:'tool-result',toolCallId:'visual-tool',content:[{type:'text',text:'Image from running-175'},image]});
  const before=JSON.stringify(fixture);
  const {calls}=await run((o,n)=>chunks('Visual or text checkpoint'),new AbortController(),fixture);
  assert.equal(calls[0].messages[0].content[1].type,'image');
  assert.deepEqual(calls[0].messages[0].content[1],image);
  assert.ok(!calls[0].messages[0].content.some(b=>b.type==='tool-result'),'isolated image request must not include orphan tool results');
  assert.equal(JSON.stringify(fixture),before);
 });
 await t.test('attachment byte size, not its small metadata, enforces the single-image boundary',async()=>{
  const fixture={messages:[createUserMessage({content:[{type:'image',attachment:{attachmentId:'sha256:fixture',name:'large.png',bytes:1048576,mimeType:'image/png'}}],source:{kind:'user'}})]};
  let invoked=false;
  await assert.rejects(()=>run(()=>{invoked=true;return chunks('wrong');},new AbortController(),fixture),e=>e.code==='COMPACTION_IMAGE_TOO_LARGE');
  assert.equal(invoked,false);
 });
 await t.test('a non-shrinking batch set fails instead of recursively looping',async()=>{
  await assert.rejects(()=>run(()=>chunks(text)),e=>e.code==='COMPACTION_NOT_SMALLER');
 });
});
test('real compaction summarizer handles limits without losing context',async t=>{
  const require=createRequire(path.join(runtime,'node_modules/@deepseek-ai/dsh/lib/bin.js'));
  const {BasicCompactionEngine}=await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-compaction-basic')).href);
  const {createUserMessage}=await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-llm')).href);
  const input={messages:[createUserMessage({content:[{type:'text',text:'Original task and evidence'}],source:{kind:'user'}})]};
  const agent={session:{id:'fixture',requestHeader:()=>({config:{provider:'fixture',model:'fixture'}})},options:{}};
  async function summarize(responses,controller=new AbortController()){
    const calls=[];
    const ctx={llm:{async *stream(options){calls.push(structuredClone({...options,signal:undefined}));yield* responses(calls.length,controller);}}};
    const config={summarizationProvider:'',summarizationModel:'',maxTokens:8192,modelPolicies:[]};
    const result=await BasicCompactionEngine.prototype.summarize.call({ctx,config},input,agent,controller.signal);
    return {result,calls};
  }
  const chunks=(text,kind='completed',usage=true)=>[
    {type:'text-delta',index:0,text},
    ...(usage?[{type:'usage',usage:{inputTokens:20,outputTokens:4,totalTokens:24}}]:[]),
    {type:'finish',reason:{kind}},
  ];
  await t.test('joins six truncated summaries and complete final output, sums usage',async()=>{
    const {result,calls}=await summarize(n=>chunks(`part ${n}\n`,n<=6?'max-tokens':'completed'));
    assert.equal(calls.length,7);assert.equal(result.usage.totalTokens,168);
    assert.equal(result.summary.map(b=>b.text).join(''),'part 1\npart 2\npart 3\npart 4\npart 5\npart 6\npart 7\n');
    assert.match(JSON.stringify(calls[6].messages),/Original task and evidence/);
    assert.match(JSON.stringify(calls[6].messages),/part 1/);
    assert.match(JSON.stringify(calls[6].messages),/part 6/);
    assert.equal(calls[6].messages.length,4,'continuation notices do not accumulate');
  });
  await t.test('reasoning-only truncation reaches a visible summary',async()=>{
    const {result,calls}=await summarize(n=>n<3?[{type:'reasoning-delta',index:0,text:`think ${n}`},{type:'finish',reason:{kind:'max-tokens'}}]:chunks('Complete checkpoint'));
    assert.equal(calls.length,3);assert.deepEqual(result.summary,[{type:'text',text:'Complete checkpoint'}]);
    assert.equal(result.usage,undefined,'missing usage is not presented as a full total');
  });
  await t.test('empty or repeated truncated content stops without a summary',async()=>{
    for(const text of ['', 'same'])await assert.rejects(()=>summarize(()=>chunks(text,'max-tokens')),e=>e.code==='COMPACTION_CONTINUE_STALLED');
  });
  await t.test('cancel between responses never produces a partial checkpoint',async()=>{
    const controller=new AbortController();let count=0;
    await assert.rejects(()=>summarize(n=>{count=n;controller.abort(new Error('fixture cancel'));return chunks('partial','max-tokens');},controller),/fixture cancel/);
    assert.equal(count,1);
  });
  await t.test('provider errors are not converted into successful partial summaries',async()=>{
    await assert.rejects(()=>summarize(n=>n===1?chunks('partial','max-tokens'):[{type:'finish',reason:{kind:'error',failure:{message:'fixture provider error',code:'FIXTURE'}}}]),e=>e.code==='FIXTURE');
  });
  await t.test('image output is rejected before replay',async()=>{
    await assert.rejects(()=>summarize(()=>[{type:'block-end',index:0,block:{type:'image',url:'fixture'}},{type:'finish',reason:{kind:'max-tokens'}}]),e=>e.code==='UNSUPPORTED_CONTENT');
  });
  await t.test('a growing non-convergent checkpoint stops before exceeding transport capacity',async()=>{
    let calls=0;
    await assert.rejects(()=>summarize(n=>{calls=n;return chunks(`part ${n} `+'a'.repeat(300000),'max-tokens');}),e=>e.code==='COMPACTION_NOT_SMALLER');
    assert.equal(calls,3);
  });
});
