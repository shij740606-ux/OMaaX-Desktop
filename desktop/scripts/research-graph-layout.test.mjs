import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';

const require=createRequire(import.meta.url);
function load(name){
 const source=fs.readFileSync(new URL('../src/renderer/src/views/'+name+'.ts',import.meta.url),'utf8');
 const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
 const mod={exports:{}};new Function('require','module','exports',compiled)(name=>name.startsWith('.')?load(name.slice(2)):require(name),mod,mod.exports);return mod.exports;
}
const {routeResearchGraph}=load('research-graph-routing'),ELK=require('elkjs/lib/elk.bundled.js');
const layoutResearchGraph=analysis=>routeResearchGraph(analysis,new ELK());
const {RESEARCH_NODE_WIDTH:width,RESEARCH_NODE_HEIGHT:height}=load('research-graph-model');
const dense=JSON.parse(fs.readFileSync(new URL('../qa/fixtures/research-dense.json',import.meta.url),'utf8'));
const segments=path=>{
 const points=[...path.matchAll(/([ML])\s+([-\d.]+)\s+([-\d.]+)/g)].map(([,command,x,y])=>({command,x:+x,y:+y}));
 return points.slice(1).flatMap((b,i)=>b.command==='L'?[[points[i],b]]:[]);
};
function verify(analysis,layout){
 const cards=analysis.nodes.map(node=>({id:node.id,...layout.positions[node.id]}));
 assert.equal(cards.length,Object.keys(layout.positions).length);
 for(let i=0;i<cards.length;i++)for(let j=i+1;j<cards.length;j++){
  const a=cards[i],b=cards[j];
  assert.ok(a.x+width<=b.x||b.x+width<=a.x||a.y+height<=b.y||b.y+height<=a.y,`${a.id} overlaps ${b.id}`);
 }
 for(const edge of analysis.edges){
  assert.ok(layout.paths[edge.id],edge.id+' has a route');
  for(const [a,b] of segments(layout.paths[edge.id])){
   assert.ok(a.x===b.x||a.y===b.y,'Orthogonal route required');
   // Test every card, including the endpoints: a route may touch the boundary,
   // but must not enter the source or target body to reach another port.
   for(const card of cards){
    const enters=a.x===b.x?a.x>card.x+0.01&&a.x<card.x+width-0.01&&Math.max(a.y,b.y)>card.y+0.01&&Math.min(a.y,b.y)<card.y+height-0.01:
     a.y>card.y+0.01&&a.y<card.y+height-0.01&&Math.max(a.x,b.x)>card.x+0.01&&Math.min(a.x,b.x)<card.x+width-0.01;
    assert.ok(!enters,`${edge.id} crosses ${card.id}`);
   }
  }
 }
}

test('dense SPL map routes cross-layer, reverse, self and duplicate relationships around all cards',async()=>{
 assert.equal(dense.nodes.length,28);assert.equal(dense.edges.length,66);
 const original=structuredClone(dense),layout=await layoutResearchGraph(dense);
 verify(dense,layout);assert.deepEqual(dense,original,'Layout never rewrites the saved research');
 assert.deepEqual(await layoutResearchGraph(dense),layout,'Stable input gives a stable layout');
});

test('maximum supported 60 nodes / 100 relationships have no card collision or route through a card',async()=>{
 const nodes=Array.from({length:60},(_,i)=>({...dense.nodes[i%dense.nodes.length],id:'max'+i}));
 const edges=Array.from({length:100},(_,i)=>({...dense.edges[i%dense.edges.length],id:'edge'+i,source:'max'+(i%60),target:'max'+((i*17+13)%60)}));
 const analysis={...dense,nodes,edges};verify(analysis,await layoutResearchGraph(analysis));
});

test('disconnected nodes and an empty saved graph can still be displayed',async()=>{
 const analysis={...dense,nodes:dense.nodes.slice(0,6),edges:[]};verify(analysis,await layoutResearchGraph(analysis));
 assert.deepEqual(await layoutResearchGraph({...analysis,nodes:[]}),{positions:{},paths:{}});
});

test('saved identifiers cannot collide with layout ports or relationship identifiers',async()=>{
 const ids=['research','research:in','research:out'];
 const nodes=ids.map((id,i)=>({...dense.nodes[i],id}));
 const edges=ids.map((id,i)=>({...dense.edges[i],id,source:ids[i],target:ids[(i+1)%ids.length]}));
 const analysis={...dense,nodes,edges};verify(analysis,await layoutResearchGraph(analysis));
});
