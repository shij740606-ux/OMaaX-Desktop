import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import {createRequire} from 'node:module';
import os from 'node:os';
import path from 'node:path';
const require=createRequire(import.meta.url);
let playwright;
try{playwright=require('playwright');}catch{playwright=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json'))('playwright');}
let browser,page;
before(async()=>{
  const sources=['figure-model','figure-vector'].map(name=>ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/views/'+name+'.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText);
  browser=await playwright.chromium.launch({channel:'msedge',headless:true});page=await browser.newPage();
  await page.route('**/*',route=>route.abort());await page.setContent('<html><body></body></html>');await page.evaluate(sources=>{const model={exports:{}},vectorModule={exports:{}};new Function('exports','module',sources[0])(model.exports,model);new Function('exports','module','require',sources[1])(vectorModule.exports,vectorModule,()=>model.exports);window.vector=vectorModule.exports;},sources);
});
after(async()=>{await browser?.close();});
const run=(fn,arg)=>page.evaluate(fn,arg);
test('raw and serialized SVG keep stable object identities, with definitions excluded',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="0 0 200 100"><defs><clipPath id="c"><rect width="100" height="100"/></clipPath></defs><g transform="translate(20 10)"><rect width="20" height="30" clip-path="url(#c)"/><text x="5" y="20">Rice</text></g></svg>';
    const objects=vector.vectorObjects(svg),changed=vector.editVector(svg,[objects.find(o=>o.tag==='text').id],{text:'Wheat'});
    return {objects,after:vector.vectorObjects(changed),round:vector.vectorObjects(vector.editVector(changed,[],{}))};
  });
  assert.equal(result.objects.length,3);assert.equal(result.objects.filter(o=>o.tag==='rect').length,1);
  assert.equal(result.objects[1].parentId,result.objects[0].id);assert.equal(result.objects[1].depth,1);
  assert.deepEqual(result.objects.map(o=>o.id),result.after.map(o=>o.id));assert.deepEqual(result.after,result.round);
});
test('selective color and text styling override inline CSS without changing same-color siblings or paths',async()=>{
  const result=await run(()=>{
    let svg='<svg viewBox="0 0 200 100"><path d="M0 0L20 20" stroke="#112233"/><text style="fill:#112233;font-size:12px" x="10" y="20">A</text><text fill="#112233" x="40" y="20">B</text></svg>';
    const id=vector.vectorObjects(svg).find(o=>o.tag==='text').id;
    svg=vector.editVector(svg,[id],{fill:'#ff0000',fontFamily:'Georgia',fontSize:18,fontWeight:700,fontStyle:'italic',textAnchor:'middle',letterSpacing:1.5,opacity:.6});
    return {svg,objects:vector.vectorObjects(svg)};
  });
  assert.equal(result.objects[1].fill,'#ff0000');assert.equal(result.objects[2].fill,'#112233');
  assert.equal(result.objects[1].fontFamily,'Georgia');assert.equal(result.objects[1].fontSize,18);
  assert.equal(result.objects[1].fontWeight,'700');assert.equal(result.objects[1].letterSpacing,1.5);assert.equal(result.objects[2].letterSpacing,0);assert.equal(result.objects[1].opacity,.6);assert.match(result.svg,/d="M0 0L20 20"/);
});

test('vector text reads and edits visible runs without replacing titles, descriptions or positioned spans',async()=>{
  const result=await run(()=>{
    const source='<svg viewBox="0 0 200 100"><text data-vector-id="label">\n <title>Main label</title><desc>Accessible description</desc>\n <tspan data-vector-id="span" x="30" y="60"><title>Span title</title>Original caption</tspan>\n</text></svg>';
    const before=vector.vectorObjects(source),changed=vector.editVector(source,['label'],{text:'Revised caption'}),doc=new DOMParser().parseFromString(changed,'image/svg+xml');
    const empty=vector.editVector(changed,['label'],{text:''}),restored=vector.editVector(empty,['label'],{text:'Restored caption'}),restoredDoc=new DOMParser().parseFromString(restored,'image/svg+xml');
    return {before,after:vector.vectorObjects(changed),titles:Array.from(doc.querySelectorAll('title'),el=>el.textContent),description:doc.querySelector('desc').textContent,span:doc.querySelector('tspan').lastChild.textContent,restored:vector.vectorObjects(restored),anchor:[restoredDoc.querySelector('tspan').getAttribute('x'),restoredDoc.querySelector('tspan').getAttribute('y')],restoredSpan:restoredDoc.querySelector('tspan').lastChild.textContent};
  });
  assert.deepEqual(result.before.map(value=>value.text),['Original caption','Original caption']);assert.equal(result.before[0].name,'Main label');
  assert.deepEqual(result.after.map(value=>value.text),['Revised caption','Revised caption']);assert.deepEqual(result.titles,['Main label','Span title']);assert.equal(result.description,'Accessible description');assert.equal(result.span,'Revised caption');
  assert.deepEqual(result.restored.map(value=>value.text),['Restored caption','Restored caption']);assert.deepEqual(result.anchor,['30','60']);assert.equal(result.restoredSpan,'Restored caption');
});

test('textPath editing preserves its reference and metadata while replacing only the displayed caption',async()=>{
  const result=await run(()=>{
    const source='<svg viewBox="0 0 200 100"><defs><path id="baseline" d="M10 40L190 40"/></defs><text data-vector-id="label"><title>Curve label</title><textPath data-vector-id="path-label" href="#baseline"><desc>Curve description</desc>Original</textPath></text></svg>',changed=vector.editVector(source,['path-label'],{text:'Edited'}),doc=new DOMParser().parseFromString(changed,'image/svg+xml');
    return {objects:vector.vectorObjects(changed),title:doc.querySelector('title').textContent,desc:doc.querySelector('desc').textContent,reference:doc.querySelector('textPath').getAttribute('href'),pathId:doc.querySelector('path').id};
  });
  assert.deepEqual(result.objects.map(value=>value.text),['Edited','Edited']);assert.equal(result.title,'Curve label');assert.equal(result.desc,'Curve description');assert.equal(result.reference,'#'+result.pathId);
});
test('nested transforms survive edits and grouping without flattening the coordinate system',async()=>{
  const result=await run(()=>{
    let svg='<svg viewBox="0 0 200 100"><g transform="translate(25 10) rotate(18)"><g transform="scale(2 .5)"><rect x="5" y="7" width="20" height="10"/><circle cx="40" cy="20" r="5"/></g></g></svg>';
    const original=vector.vectorObjects(svg),ids=original.filter(o=>['rect','circle'].includes(o.tag)).map(o=>o.id);
    const grouped=vector.groupVectorNodes(svg,ids),ungrouped=vector.ungroupVectorNodes(grouped.svg,[grouped.id]);
    function matrix(markup,id){const host=document.createElement('div');host.innerHTML=markup;const root=host.firstElementChild;root.setAttribute('width','400');root.setAttribute('height','200');document.body.append(host);const m=root.querySelector('[data-vector-id="'+id+'"]').getCTM();const values=[m.a,m.b,m.c,m.d,m.e,m.f];host.remove();return values;}
    const normalized=vector.editVector(svg,[],{});
    return {before:matrix(normalized,ids[0]),grouped:matrix(grouped.svg,ids[0]),after:matrix(ungrouped.svg,ids[0]),ids:ungrouped.ids,expected:ids,objects:vector.vectorObjects(ungrouped.svg)};
  });
  assert.deepEqual(result.grouped,result.before);assert.deepEqual(result.after,result.before);assert.deepEqual(result.ids,result.expected);
  assert.equal(result.objects.length,4);
});
test('ungroup keeps shared opacity, filters and clipping context instead of changing compositing',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="0 0 100 100"><defs><filter id="f"><feGaussianBlur stdDeviation="2"/></filter><clipPath id="c" clipPathUnits="objectBoundingBox"><rect width="1" height=".5"/></clipPath></defs><g opacity=".5" filter="url(#f)" clip-path="url(#c)"><rect width="50" height="50"/><circle cx="30" cy="30" r="20"/></g></svg>';
    const group=vector.vectorObjects(svg).find(o=>o.tag==='g'),result=vector.ungroupVectorNodes(svg,[group.id]);
    const doc=new DOMParser().parseFromString(result.svg,'image/svg+xml'),wrapper=doc.querySelector('[data-vector-wrapper="true"]');
    return {objects:vector.vectorObjects(result.svg),ids:result.ids,opacity:wrapper.getAttribute('opacity'),filter:wrapper.getAttribute('filter'),clip:wrapper.getAttribute('clip-path'),units:doc.querySelector('clipPath').getAttribute('clipPathUnits')};
  });
  assert.equal(result.objects.length,2);assert.equal(result.ids.length,2);assert.equal(result.opacity,'.5');assert.match(result.filter,/url\(#/);assert.match(result.clip,/url\(#/);assert.equal(result.units,'objectBoundingBox');
});
test('locks prevent edits and structural operations through a selected parent',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="0 0 100 100"><g><rect width="20" height="20" data-vector-locked="true"/><circle cx="40" cy="40" r="10"/></g></svg>';
    const objects=vector.vectorObjects(svg),parent=objects[0].id,locked=objects[1].id;
    const attempts=[()=>vector.editVector(svg,[parent],{transform:'translate(2 3)'}),()=>vector.deleteVectorNodes(svg,[parent]),()=>vector.duplicateVectorNodes(svg,[parent]),()=>vector.ungroupVectorNodes(svg,[parent])];
    return {rejected:attempts.map(fn=>{try{fn();return false;}catch{return true;}}),unlocked:vector.vectorObjects(vector.editVector(svg,[locked],{locked:false})).find(o=>o.id===locked).locked};
  });
  assert.deepEqual(result.rejected,[true,true,true,true]);assert.equal(result.unlocked,false);
});
test('duplicate remaps internal IDs and references while retaining external definitions',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="0 0 100 100"><defs><linearGradient id="paint"><stop stop-color="red"/></linearGradient></defs><g><path id="shape" d="M0 0L10 10"/><use href="#shape"/><rect width="20" height="20" fill="url(#paint)"/></g></svg>';
    const objects=vector.vectorObjects(svg),copy=vector.duplicateVectorNodes(svg,[objects[0].id]),doc=new DOMParser().parseFromString(copy.svg,'image/svg+xml');
    return {ids:Array.from(doc.querySelectorAll('[id]'),n=>n.id),links:Array.from(doc.querySelectorAll('use'),n=>n.getAttribute('href').slice(1)),vectors:vector.vectorObjects(copy.svg).map(o=>o.id),selected:copy.ids};
  });
  assert.equal(new Set(result.ids).size,result.ids.length);assert.equal(new Set(result.vectors).size,result.vectors.length);assert.notEqual(result.links[0],result.links[1]);assert.ok(result.links.every(id=>result.ids.includes(id)));assert.equal(result.selected.length,1);
});
test('cross-SVG paste imports referenced definitions and preserves ancestor transform/style/clip',async()=>{
  const result=await run(()=>{
    const source='<svg viewBox="0 0 200 100"><defs><linearGradient id="paint"><stop stop-color="blue"/></linearGradient><clipPath id="clip" clipPathUnits="userSpaceOnUse"><rect width="100" height="100"/></clipPath></defs><g transform="translate(30 15)" fill="url(#paint)" clip-path="url(#clip)" opacity=".7"><g transform="rotate(12)"><rect width="30" height="20"/></g></g></svg>';
    const target='<svg viewBox="0 0 200 100"><defs><linearGradient id="paint"><stop stop-color="red"/></linearGradient></defs><circle r="5"/></svg>';
    const objects=vector.vectorObjects(source),id=objects.find(o=>o.tag==='rect').id,pasted=vector.pasteVectorNodes(target,source,[id]);
    function inspect(markup,nodeId){const host=document.createElement('div');host.innerHTML=markup;const root=host.firstElementChild;root.setAttribute('width','400');root.setAttribute('height','200');document.body.append(host);const el=root.querySelector('[data-vector-id="'+nodeId+'"]'),m=el.getScreenCTM(),style=getComputedStyle(el);const result={matrix:[m.a,m.b,m.c,m.d,m.e,m.f],fill:style.fill};host.remove();return result;}
    const parsed=new DOMParser().parseFromString(pasted.svg,'image/svg+xml'),ids=Array.from(parsed.querySelectorAll('[id]'),n=>n.id);
    return {before:inspect(vector.editVector(source,[],{}),id),after:inspect(pasted.svg,pasted.ids[0]),ids,objects:vector.vectorObjects(pasted.svg),svg:pasted.svg};
  });
  assert.deepEqual(result.after.matrix,result.before.matrix);assert.equal(new Set(result.ids).size,result.ids.length);assert.equal(result.objects.filter(o=>o.tag==='rect').length,1);assert.match(result.svg,/stop-color="blue"/);assert.match(result.svg,/stop-color="red"/);assert.match(result.svg,/clipPathUnits="userSpaceOnUse"/);assert.match(result.svg,/opacity=".7"/);
});
test('invalid transforms, external colors and arbitrary attributes cannot enter the SVG',async()=>{
  const rejected=await run(()=>{
    const svg='<svg viewBox="0 0 10 10"><rect width="10" height="10"/></svg>',id=vector.vectorObjects(svg)[0].id;
    return [{transform:'translate(NaN 0)'},{fill:'url(https://example.test/a)'},{onload:'alert(1)'},{opacity:2},{fontFamily:'url(https://example.test/font)'}].map(patch=>{try{vector.editVector(svg,[id],patch);return false;}catch{return true;}});
  });
  assert.deepEqual(rejected,[true,true,true,true,true]);
});
test('hidden objects remain addressable and may be restored without losing their identity',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="0 0 10 10"><rect width="10" height="10"/></svg>',id=vector.vectorObjects(svg)[0].id,hidden=vector.editVector(svg,[id],{hidden:true}),restored=vector.editVector(hidden,[id],{hidden:false});
    return {id,hidden:vector.vectorObjects(hidden)[0],restored:vector.vectorObjects(restored)[0]};
  });
  assert.equal(result.hidden.hidden,true);assert.equal(result.restored.hidden,false);assert.equal(result.restored.id,result.id);
});
test('deleting a referenced shape cannot silently erase unselected use instances',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="0 0 20 20"><path id="p" d="M0 0L10 10"/><use href="#p"/></svg>',objects=vector.vectorObjects(svg);
    let rejected=false;try{vector.deleteVectorNodes(svg,[objects[0].id]);}catch{rejected=true;}
    return {rejected,remaining:vector.vectorObjects(vector.deleteVectorNodes(svg,objects.map(item=>item.id))).length};
  });
  assert.equal(result.rejected,true);assert.equal(result.remaining,0);
});
test('front, back and one-step reordering preserve selection order and definition slots',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="0 0 100 100"><title>Order</title><defs><clipPath id="clip"><rect width="100" height="100"/></clipPath></defs><rect data-vector-id="a" width="10" height="10"/><rect data-vector-id="b" width="20" height="20"/><rect data-vector-id="c" width="30" height="30"/><rect data-vector-id="d" width="40" height="40" clip-path="url(#clip)"/></svg>';
    return Object.fromEntries(['front','back','forward','backward'].map(action=>{
      const changed=vector.reorderVectorNodes(svg,['d','b'],action),doc=new DOMParser().parseFromString(changed,'image/svg+xml');
      return [action,{order:vector.vectorObjects(changed).map(item=>item.id),prefix:Array.from(doc.documentElement.children).slice(0,2).map(node=>node.localName),clips:doc.querySelectorAll('clipPath').length}];
    }));
  });
  assert.deepEqual(result.front.order,['a','c','b','d']);assert.deepEqual(result.back.order,['b','d','a','c']);assert.deepEqual(result.forward.order,['a','c','b','d']);assert.deepEqual(result.backward.order,['b','a','d','c']);
  for(const value of Object.values(result)){assert.deepEqual(value.prefix,['title','defs']);assert.equal(value.clips,1);}
});
test('reordering removes redundant descendant selection and rejects cross-parent or locked content',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="0 0 100 100"><g data-vector-id="first"><rect data-vector-id="child" width="10" height="10"/></g><circle data-vector-id="other" r="8"/><g data-vector-id="protected"><rect data-vector-id="locked" data-vector-locked="true" width="20" height="20"/></g></svg>';
    const changed=vector.reorderVectorNodes(svg,['child','first'],'front');
    const rejected=[()=>vector.reorderVectorNodes(svg,['child','other'],'front'),()=>vector.reorderVectorNodes(svg,['protected'],'back'),()=>vector.reorderVectorNodes(svg,['locked'],'front')].map(fn=>{try{fn();return false;}catch{return true;}});
    return {order:vector.vectorObjects(changed).filter(item=>item.parentId===null).map(item=>item.id),child:vector.vectorObjects(changed).find(item=>item.id==='child').parentId,rejected};
  });
  assert.deepEqual(result.order,['other','protected','first']);assert.equal(result.child,'first');assert.deepEqual(result.rejected,[true,true,true]);
});
test('selected SVG extraction includes only selected content and required definitions with the original viewBox',async()=>{
  const result=await run(()=>{
    const svg='<svg viewBox="10 20 200 100"><defs><linearGradient id="needed"><stop stop-color="blue"/></linearGradient><linearGradient id="unused"><stop stop-color="red"/></linearGradient><clipPath id="clip"><rect width="100" height="100"/></clipPath></defs><g transform="translate(30 15)" fill="url(#needed)" clip-path="url(#clip)" opacity=".7"><rect data-vector-id="chosen" width="30" height="20"/><circle data-vector-id="not-chosen" r="8"/></g><text data-vector-id="unrelated">Do not copy</text></svg>';
    const selected=vector.selectedVectorSvg(svg,['chosen']),doc=new DOMParser().parseFromString(selected,'image/svg+xml');
    function matrix(markup,id){const host=document.createElement('div');host.innerHTML=markup;const root=host.firstElementChild;root.setAttribute('width','400');root.setAttribute('height','200');document.body.append(host);const m=root.querySelector('[data-vector-id="'+id+'"]').getScreenCTM();const value=[m.a,m.b,m.c,m.d,m.e,m.f];host.remove();return value;}
    const objects=vector.vectorObjects(selected);
    return {svg:selected,viewBox:doc.documentElement.getAttribute('viewBox'),objects,before:matrix(vector.editVector(svg,[],{}),'chosen'),after:matrix(selected,objects[0].id),gradients:doc.querySelectorAll('linearGradient').length,clips:doc.querySelectorAll('clipPath').length};
  });
  assert.equal(result.viewBox,'10 20 200 100');assert.equal(result.objects.length,1);assert.equal(result.objects[0].tag,'rect');assert.deepEqual(result.after,result.before);assert.equal(result.gradients,1);assert.equal(result.clips,1);
  assert.doesNotMatch(result.svg,/Do not copy|not-chosen|stop-color="red"/);assert.match(result.svg,/stop-color="blue"/);assert.match(result.svg,/opacity=".7"/);
});
