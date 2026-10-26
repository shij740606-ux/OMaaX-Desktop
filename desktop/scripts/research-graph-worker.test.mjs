import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {createRequire} from 'node:module';

const require=createRequire(import.meta.url);
const graph={nodes:[{id:'n1'}],edges:[{id:'e1',source:'n1',target:'n1'}]},valid={positions:{n1:{x:0,y:0}},paths:{e1:'M 0 0 L 1 0'}};
const routed={children:[{id:'node/n1',x:0,y:0}],edges:[{id:'edge/e1',sections:[{startPoint:{x:0,y:0},endPoint:{x:1,y:0}}]}]};
function localModule(name){
 const source=fs.readFileSync(new URL('../src/renderer/src/views/'+name.slice(2)+'.ts',import.meta.url),'utf8');
 const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
 const mod={exports:{}};new Function('require','module','exports',compiled)(name=>name.startsWith('.')?localModule(name):require(name),mod,mod.exports);return mod.exports;
}
function fixture(){
 const workers=[],timers=new Map();let sequence=0;
 class Worker{
  constructor(url){this.url=String(url);this.terminated=0;workers.push(this);}
  postMessage(value){this.input=value;}
  terminate(){this.terminated++;}
 }
 const source=fs.readFileSync(new URL('../src/renderer/src/views/research-graph-layout.ts',import.meta.url),'utf8').replace('import.meta.url',JSON.stringify('file:///research/research-graph-layout.ts'));
 const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
 const module={exports:{}};
 new Function('require','module','exports','Worker','setTimeout','clearTimeout',compiled)(name=>name.startsWith('.')?localModule(name):require(name),module,module.exports,Worker,callback=>{timers.set(++sequence,callback);return sequence;},id=>timers.delete(id));
 return {run:module.exports.layoutResearchGraph,workers,timers};
}

test('layout uses a dedicated worker and terminates it after a complete response',async()=>{
 const h=fixture(),promise=h.run(graph),worker=h.workers[0];
 assert.match(worker.url,/research-graph-layout.worker.ts$/);assert.equal(worker.input.cmd,'layout');assert.equal(worker.input.graph.children[0].id,'node/n1');
 worker.onmessage({data:{id:worker.input.id,data:routed}});assert.deepEqual(await promise,valid);
 assert.equal(worker.terminated,1);assert.equal(h.timers.size,0);
});

test('cancelled layouts terminate immediately and late worker output is ignored',async()=>{
 const h=fixture(),controller=new AbortController(),promise=h.run(graph,controller.signal),worker=h.workers[0];
 controller.abort();await assert.rejects(promise,{name:'AbortError'});
 worker.onmessage({data:{id:worker.input.id,data:routed}});assert.equal(worker.terminated,1);assert.equal(h.timers.size,0);
 await assert.rejects(h.run(graph,controller.signal),{name:'AbortError'});assert.equal(h.workers.length,1);
});

test('incomplete layouts and worker load errors reject locally and release resources',async()=>{
 for(const failure of ['missing position','worker error','message error']){
  const h=fixture(),promise=h.run(graph),worker=h.workers[0];
  if(failure==='missing position')worker.onmessage({data:{id:worker.input.id,data:{children:[],edges:[]}}});
  else if(failure==='worker error')worker.onerror({preventDefault(){}});
  else worker.onmessageerror();
  await assert.rejects(promise,/Research layout/);assert.equal(worker.terminated,1);assert.equal(h.timers.size,0);
 }
});

test('an unresponsive layout worker times out and can be retried with a fresh worker',async()=>{
 const h=fixture(),promise=h.run(graph);[...h.timers.values()][0]();
 await assert.rejects(promise,/timed out/);assert.equal(h.workers[0].terminated,1);
 const retry=h.run(graph);h.workers[1].onmessage({data:{id:h.workers[1].input.id,data:routed}});assert.deepEqual(await retry,valid);
});
