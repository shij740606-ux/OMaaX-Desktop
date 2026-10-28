import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
let playwright;
try { playwright = require('playwright'); }
catch { playwright = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json'))('playwright'); }
const source = ts.transpileModule(fs.readFileSync(new URL('../src/renderer/src/views/figure-model.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
let browser, page;
const calls = [];
before(async () => {
  browser = await playwright.chromium.launch({ channel: 'msedge', headless: true });
  page = await browser.newPage();
  await page.route('**/*', route => { calls.push(route.request().url()); return route.abort(); });
  await page.setContent('<!doctype html><html><body></body></html>');
  await page.evaluate(source => { const module = { exports: {} }; new Function('exports', 'module', source)(module.exports, module); window.figure = module.exports; }, source);
});
after(async () => { await browser?.close(); });
const run = (fn, args) => page.evaluate(fn, args);

test('untrusted SVG cannot retain active content or external resources', async () => {
  const clean = await run(() => window.figure.sanitizeSvg(`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="300" height="200" onload="alert(1)"><script>alert(1)</script><foreignObject><iframe src="https://example.test"/></foreignObject><style>@import url(https://example.test/font);.red{fill:red;background:url(https://example.test/x)}</style><rect class="red" width="10" height="20" onclick="alert(1)"/><image href="https://example.test/image"/><image href="data:image/svg+xml;base64,PHN2Zy8+"/><use xlink:href="https://example.test/a.svg#x"/><animate attributeName="href" to="https://example.test/a"/><path d="M 0 0\nL 10 10" style="stroke:url(https://example.test/paint);fill:#fff"/><text>文字 Δ</text></svg>`));
  assert.doesNotMatch(clean, /script|foreignObject|iframe|https:\/\/example|onload|onclick|animate|@import|data:image\/svg/i);
  assert.match(clean, /fill:red/); assert.match(clean, /文字 Δ/); assert.match(clean, /L 10 10/);
});
test('XML entities rejected and SVG without namespace is normalized', async () => {
  const result = await run(() => {
    let rejected = false; try { window.figure.sanitizeSvg('<!DOCTYPE svg [<!ENTITY x "test">]><svg/>'); } catch { rejected = true; }
    return { rejected, clean: window.figure.sanitizeSvg('<svg width="20" height="40"><text x="2">Hello</text></svg>') };
  });
  assert.equal(result.rejected, true); assert.match(result.clean, /Hello/); assert.match(result.clean, /viewBox="0 0 20 40"/);
});
test('standard plot SVG declaration is removed without requesting its external DTD', async () => {
  const clean = await run(() => window.figure.sanitizeSvg('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg xmlns="http://www.w3.org/2000/svg" width="432pt" height="288pt"><path d="M 0 0 L 10 10"/><text>Plot</text></svg>'));
  assert.doesNotMatch(clean, /DOCTYPE|w3.org\/Graphics/); assert.match(clean, /viewBox="0 0 576 384"/); assert.match(clean, /Plot/);
});
test('internal gradients, use and clipping survive independent panel imports', async () => {
  const svg = await run(() => {
    const f = window.figure, source = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 150"><defs><linearGradient id="paint"><stop stop-color="#abc"/></linearGradient><clipPath id="crop"><rect width="100" height="100"/></clipPath><path id="shape" d="M0 0L10 10"/></defs><use href="#shape"/><rect width="300" height="150" fill="url(#paint)" clip-path="url(#crop)"/></svg>';
    const a = f.importSvg(source), b = f.importSvg(source); b.x = 400;
    return f.figureSvg({ ...f.blankFigure(), items: [a, b] });
  });
  const ids = [...svg.matchAll(/\sid="([^"]+)"/g)].map(m => m[1]);
  assert.equal(new Set(ids).size, ids.length); assert.equal(ids.length, 6);
  for (const m of svg.matchAll(/(?:href="#|url\(#)([\w-]+)/g)) assert.ok(ids.includes(m[1]));
  assert.match(svg, /<linearGradient/); assert.match(svg, /<clipPath/); assert.doesNotMatch(svg, /<image/);
});
test('project roundtrip preserves grouping, rotation, labels and more than 40 panels', async () => {
  const result = await run(() => {
    const f = window.figure, item = f.importSvg('<svg viewBox="0 0 120 60"><text>Sample</text></svg>');
    const doc = f.parseFigure({ ...f.blankFigure('Study'), widthMm: 170, items: Array.from({ length: 55 }, (_, i) => ({ ...item, id: `p${i}`, groupId: i < 2 ? 'pair' : undefined, rotation: 45, x: i * 20 })) });
    return { before: doc, after: f.parseFigure(JSON.stringify(doc)) };
  });
  assert.deepEqual(result.after, result.before); assert.equal(result.after.items.length, 55); assert.equal(result.after.items[0].groupId, 'pair');
});
test('Unicode panel identifiers cannot cause SVG definition collisions', async () => {
  const svg = await run(() => {
    const f = window.figure, a = f.importSvg('<svg viewBox="0 0 100 100"><defs><path id="p" d="M0 0L10 10"/></defs><use href="#p"/></svg>');
    return f.figureSvg(f.parseFigure({ ...f.blankFigure(), items: [{ ...a, id: '甲' }, { ...a, id: '乙' }] }));
  });
  const ids = [...svg.matchAll(/\sid="([^"]+)"/g)].map(m => m[1]); assert.equal(ids.length, 2); assert.equal(new Set(ids).size, 2);
});
test('bad dimensions, duplicates and disguised raster content fail validation', async () => {
  const result = await run(() => {
    const f = window.figure, a = { ...f.importSvg('<svg viewBox="0 0 10 10"/>'), id: 'a' }, base = f.blankFigure();
    return [
      () => f.parseFigure({ ...base, width: Infinity }),
      () => f.parseFigure({ ...base, items: [a, a] }),
      () => f.parseFigure({ ...base, items: [{ ...a, x: NaN }] }),
      () => f.parseFigure({ ...base, items: [{ ...a, kind: 'image', dataUrl: 'data:image/png;base64,PHN2Zy8+' }] }),
    ].map(fn => { try { fn(); return false; } catch { return true; } });
  });
  assert.deepEqual(result, [true, true, true, true]);
});
test('text and color edits retain paths, clipping and escaped Unicode', async () => {
  const result = await run(() => {
    const f = window.figure, source = '<svg viewBox="0 0 200 100"><defs><clipPath id="c"><rect width="200" height="100"/></clipPath></defs><g clip-path="url(#c)"><path d="M0 0L100 20" stroke="#123456"/><text fill="#123456"><tspan>A</tspan><tspan>B</tspan></text></g></svg>';
    const edited = f.replaceSvgColor(f.replaceSvgText(source, 0, '水稻 <5 & Δ'), '#123456', '#cc0044');
    return { edited, entries: f.svgTextEntries(edited), colors: f.svgColors(edited) };
  });
  assert.equal(result.entries[0].text, '水稻 <5 & Δ'); assert.deepEqual(result.colors, ['#cc0044']);
  assert.match(result.edited, /<path/); assert.match(result.edited, /clip-path="url\(#/); assert.match(result.edited, /&lt;5 &amp; Δ/);
});
test('whole-label text editing preserves its original tspan anchor and formatting', async () => {
  const clean = await run(() => window.figure.replaceSvgText('<svg viewBox="0 0 200 100"><text>\n<tspan x="30" y="60" font-weight="bold">Old</tspan><tspan> label</tspan></text></svg>', 0, 'New label'));
  assert.match(clean, /<tspan x="30" y="60" font-weight="bold"[^>]*>New label<\/tspan>/); assert.doesNotMatch(clean, /Old/);
});

test('whole-label edits ignore descriptive metadata and retain indented or empty positioned spans', async () => {
  const result = await run(() => {
    const f=window.figure;
    return ['Original caption',''].map(caption=>{
      const source='<svg viewBox="0 0 200 100"><text>\n  <title>Main label</title><desc>Accessible description</desc>\n  <tspan x="30" y="60" font-weight="bold"><title>Span title</title>'+caption+'</tspan>\n</text></svg>';
      const before=f.svgTextEntries(source),edited=f.replaceSvgText(source,0,'Revised caption'),doc=new DOMParser().parseFromString(edited,'image/svg+xml');
      return {before,after:f.svgTextEntries(edited),title:Array.from(doc.querySelectorAll('title'),el=>el.textContent),description:doc.querySelector('desc').textContent,span:doc.querySelector('tspan').lastChild.textContent,x:doc.querySelector('tspan').getAttribute('x'),y:doc.querySelector('tspan').getAttribute('y')};
    });
  });
  assert.deepEqual(result.map(value=>value.before[0].text),['Original caption','']);
  for(const value of result){assert.equal(value.after[0].text,'Revised caption');assert.deepEqual(value.title,['Main label','Span title']);assert.equal(value.description,'Accessible description');assert.equal(value.span,'Revised caption');assert.equal(value.x,'30');assert.equal(value.y,'60');}
});
test('grid, align and move treat groups as units and keep labels attached', async () => {
  const result = await run(() => {
    const f = window.figure, base = f.blankFigure(), item = { ...f.importSvg('<svg viewBox="0 0 100 50"/>'), x: 100, y: 100 };
    let doc = { ...base, items: [{ ...item, id: 'a', groupId: 'g' }, { ...item, id: 'b', x: 220, groupId: 'g' }, { ...item, id: 'c', x: 500, y: 200 }, { ...item, id: 'locked', x: 900, locked: true }] };
    doc = f.addPanelLabels(doc, ['a', 'b', 'c']); const moved = f.moveItems(doc, ['a'], 20, 30);
    const grid = f.gridLayout(moved, ['a', 'c', 'locked'], 1, 40), aligned = f.alignItems(grid, ['a', 'c'], 'left');
    return { moved, grid, aligned };
  });
  const a = result.moved.items.find(n => n.id === 'a'), b = result.moved.items.find(n => n.id === 'b'), label = result.moved.items.find(n => n.labelFor === 'a');
  assert.equal(a.x, 120); assert.equal(a.y, 130); assert.equal(b.x - a.x, 120); assert.equal(label.x, a.x); assert.equal(label.y, a.y - 32);
  assert.equal(result.grid.items.find(n => n.id === 'locked').x, 900);
  assert.equal(result.aligned.items.find(n => n.id === 'a').x, result.aligned.items.find(n => n.id === 'c').x);
});
test('fit includes rotated content and labels without clipping', async () => {
  const result = await run(() => {
    const f = window.figure, a = { ...f.importSvg('<svg viewBox="0 0 200 100"/>'), rotation: 90, x: -100, y: -100 };
    const doc = f.fitFigure(f.addPanelLabels({ ...f.blankFigure(), items: [a] }, [a.id]), 20);
    return doc;
  });
  assert.ok(result.width >= 140); assert.ok(result.height >= 240); assert.ok(result.items.find(n => n.labelFor).y >= 20);
});

test('fit measures old undersized multiline text and preserves its rotated glyph bounds', async () => {
  const results=await run(()=>{
    const f=window.figure;
    return [0,37,90].map(rotation=>{
      const item={id:'old-text',name:'Old text',kind:'text',text:'Very long annotation: gjpq\n第二行研究标签\nThird line',fontSize:32,color:'#222222',x:-70,y:-40,width:30,height:20,rotation,hidden:false,locked:false};
      const expanded=f.fitTextBox(item),doc=f.fitFigure({...f.blankFigure(),items:[item]},20);
      const container=document.createElement('div');container.innerHTML=f.figureSvg(doc);document.body.appendChild(container);
      const svg=container.querySelector('svg'),box=svg.getBoundingClientRect(),glyph=svg.querySelector('text').getBoundingClientRect();
      const result={expanded,doc,inside:glyph.left>=box.left&&glyph.top>=box.top&&glyph.right<=box.right&&glyph.bottom<=box.bottom};container.remove();return result;
    });
  });
  for(const result of results){assert.ok(result.expanded.width>300);assert.ok(result.expanded.height>100);assert.equal(result.inside,true,JSON.stringify(result));assert.equal(result.doc.items[0].width,30,'fitting old projects does not rewrite their stored layer geometry');}
});

test('adding panel labels again preserves custom content, geometry and locked labels', async () => {
  const result = await run(() => {
    const f=window.figure, panel=f.importSvg('<svg viewBox="0 0 200 100"/>');
    const first=f.addPanelLabels({...f.blankFigure(),items:[{...panel,id:'first'},{...panel,id:'second',x:300}]},['first']);
    const custom={...first.items.at(-1),name:'Publication label',text:'Fig. 3α',x:47,y:13,width:110,height:44,rotation:8,fontSize:31,color:'#987654',locked:true};
    const before={...first,items:[...first.items.slice(0,-1),custom]};
    const added=f.addPanelLabels(before,['first','second']),again=f.addPanelLabels(added,['first','second']);
    return {before,added,again,unchanged:again===added,svg:f.figureSvg(again)};
  });
  assert.deepEqual(result.added.items.slice(0,result.before.items.length),result.before.items);
  assert.equal(result.added.items.length,result.before.items.length+1);
  assert.equal(result.added.items.at(-1).labelFor,'second');assert.equal(result.added.items.at(-1).text,'B');
  assert.equal(result.unchanged,true);assert.deepEqual(result.again,result.added);
  assert.match(result.svg,/Fig\. 3α/);
});
test('hidden panels and selection outlines never appear in normal export', async () => {
  const result = await run(() => {
    const f = window.figure, a = f.importSvg('<svg viewBox="0 0 30 20"><text>visible</text></svg>'), b = { ...a, id: 'hidden', hidden: true, svg: '<svg viewBox="0 0 30 20"><text>secret</text></svg>' }, doc = { ...f.blankFigure(), items: [a, b] };
    return { plain: f.figureSvg(doc), selected: f.figureSvg(doc, [a.id]) };
  });
  assert.match(result.plain, /visible/); assert.doesNotMatch(result.plain, /secret|stroke-dasharray/); assert.match(result.selected, /stroke-dasharray/);
});
test('PNG export uses physical width and writes the correct 300 dpi metadata', async () => {
  const result = await run(async () => {
    const f = window.figure, doc = { ...f.blankFigure(), width: 300, height: 200, widthMm: 25.4, items: [f.importSvg('<svg viewBox="0 0 300 200"><rect width="300" height="200" fill="#336699"/></svg>')] };
    return f.exportFigurePng(doc, 300);
  });
  const bytes = Buffer.from(result.split(',')[1], 'base64'); assert.equal(bytes.readUInt32BE(16), 300); assert.equal(bytes.readUInt32BE(20), 200);
  let phys; for (let i = 8; i < bytes.length;) { const length = bytes.readUInt32BE(i); if (bytes.toString('ascii', i + 4, i + 8) === 'pHYs') phys = bytes.subarray(i + 8, i + 8 + length); i += length + 12; }
  assert.ok(phys); assert.equal(phys.readUInt32BE(0), 11811); assert.equal(phys.readUInt32BE(4), 11811); assert.equal(phys[8], 1);
});
test('oversized export errors clearly and local editing makes no network calls', async () => {
  const message = await run(async () => { try { await window.figure.exportFigurePng({ ...window.figure.blankFigure(), widthMm: 2000 }, 600); } catch (e) { return e.message; } });
  assert.match(message, /16000 像素|6400 万像素/); assert.deepEqual(calls, []);
});
test('version one extensions roundtrip while old documents retain their defaults',async()=>{
  const result=await run(()=>{
    const f=window.figure,old=f.parseFigure(f.blankFigure()),item={id:'styled',name:'Styled text',kind:'text',x:5,y:8,width:120,height:60,rotation:12,hidden:false,locked:false,text:'Alpha\nBeta',fontSize:20,fontFamily:'Georgia',fontWeight:700,fontStyle:'italic',textAnchor:'middle',lineHeight:1.8,letterSpacing:1.5,opacity:.6};
    const document=f.parseFigure({...old,background:'transparent',unit:'mm',items:[item]});
    return {document,round:f.parseFigure(JSON.stringify(document)),old:f.figureSvg(old),svg:f.figureSvg(document)};
  });
  assert.deepEqual(result.round,result.document);assert.match(result.old,/fill="white"/);assert.doesNotMatch(result.svg,/<rect width="1200" height="850" fill=/);
  assert.match(result.svg,/font-family="Georgia"/);assert.match(result.svg,/font-weight="700"/);assert.match(result.svg,/font-style="italic"/);assert.match(result.svg,/letter-spacing="1.5"/);assert.match(result.svg,/opacity="0.6"/);assert.match(result.svg,/y="56"/);
});
test('percentage cropping changes source viewBox and preserves the full original source for reopening',async()=>{
  const result=await run(()=>{
    const f=window.figure,item=f.importSvg('<svg viewBox="10 20 200 100"><path d="M10 20L210 120"/></svg>');
    const doc=f.parseFigure({...f.blankFigure(),items:[{...item,crop:{x:25,y:10,width:50,height:80},preserveAspectRatio:'none'}]});
    return {svg:f.figureSvg(doc),round:f.parseFigure(JSON.stringify(doc)).items[0]};
  });
  assert.match(result.svg,/viewBox="60 30 100 80"/);assert.match(result.svg,/preserveAspectRatio="none"/);assert.match(result.svg,/overflow="hidden"/);
  assert.match(result.round.svg,/viewBox="10 20 200 100"/);assert.deepEqual(result.round.crop,{x:25,y:10,width:50,height:80});
});
test('static filters and clipping units survive import, while actual removals are reported',async()=>{
  const result=await run(()=>window.figure.importSvgWithReport('<svg viewBox="0 0 100 100"><defs><filter id="blur" filterUnits="userSpaceOnUse" primitiveUnits="objectBoundingBox"><feGaussianBlur stdDeviation="2"/><feColorMatrix type="saturate" values=".5"/></filter><clipPath id="clip" clipPathUnits="objectBoundingBox"><rect width="1" height=".5"/></clipPath><mask id="m" maskUnits="userSpaceOnUse" maskContentUnits="objectBoundingBox"><rect width="1" height="1" fill="white"/></mask></defs><rect width="50" height="50" filter="url(#blur)" clip-path="url(#clip)" mask="url(#m)"/><script>alert(1)</script><foreignObject/><rect onclick="evil()" custom-unsupported="1"/></svg>'));
  assert.match(result.item.svg,/feGaussianBlur/);assert.match(result.item.svg,/filterUnits="userSpaceOnUse"/);assert.match(result.item.svg,/clipPathUnits="objectBoundingBox"/);assert.match(result.item.svg,/maskContentUnits="objectBoundingBox"/);
  assert.ok(result.report.removedElements.includes('script'));assert.ok(result.report.removedElements.includes('foreignObject'));assert.ok(result.report.removedAttributes.includes('rect.onclick'));assert.ok(result.report.removedAttributes.includes('rect.custom-unsupported'));assert.ok(result.report.warnings.length);
  assert.doesNotMatch(result.item.svg,/script|onclick|foreignObject|custom-unsupported/);
});
test('ellipse, line and arrow are editable native vector layers with serializable opacity and stroke width',async()=>{
  const result=await run(()=>{
    const f=window.figure,items=['ellipse','line','arrow'].map((kind,i)=>({id:'shape-'+i,name:kind,kind,x:i*100,y:0,width:80,height:50,rotation:0,hidden:false,locked:false,stroke:'#336699',fill:'none',strokeWidth:3,opacity:.8}));
    const doc=f.parseFigure({...f.blankFigure(),items});
    return {svg:f.figureSvg(doc),items:f.parseFigure(JSON.stringify(doc)).items};
  });
  assert.match(result.svg,/<ellipse/);assert.equal((result.svg.match(/<line /g)||[]).length,2);assert.match(result.svg,/<marker /);assert.match(result.svg,/stroke-width="3"/);assert.deepEqual(result.items.map(item=>item.kind),['ellipse','line','arrow']);
});
test('PNG transparent background preserves alpha and vector layer opacity',async()=>{
  const result=await run(async()=>{
    const f=window.figure,doc={...f.blankFigure(),width:100,height:100,widthMm:25.4,background:'transparent',items:[{id:'box',name:'box',kind:'rect',x:0,y:0,width:30,height:30,rotation:0,hidden:false,locked:false,fill:'#ff0000',stroke:'none',opacity:.5}]};
    const data=await f.exportFigurePng(doc,100),image=new Image();image.src=data;await image.decode();const canvas=document.createElement('canvas');canvas.width=100;canvas.height=100;const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
    return {inside:[...ctx.getImageData(10,10,1,1).data],outside:[...ctx.getImageData(90,90,1,1).data]};
  });
  assert.equal(result.outside[3],0);assert.ok(result.inside[3]>=126&&result.inside[3]<=129);assert.equal(result.inside[0],255);
});
test('source metadata roundtrips independently of editing, and unsupported containers are explicitly reported',async()=>{
  const result=await run(()=>{
    const f=window.figure,imported=f.importSvgWithReport('<svg viewBox="0 0 50 50"><defs><filter id="f"><feGaussianBlur stdDeviation="1"/><feImage href="#unknown"/></filter></defs><rect width="20" height="20" filter="url(#f)"/><switch><text>Conditional content</text></switch><foreignObject><div xmlns="http://www.w3.org/1999/xhtml">HTML</div></foreignObject></svg>','原始图.svg');
    const old=f.blankFigure(),doc=f.parseFigure({...old,items:[{...imported.item,source:{name:'研究论文.pdf',kind:'pdf',page:3}}]});
    const invalid=[{name:'x',kind:'other'},{name:'',kind:'svg'},{name:'x.pdf',kind:'pdf',page:0},{name:'x.pdf',kind:'pdf',page:1.5}].map(source=>{try{f.parseFigure({...old,items:[{...imported.item,source}]});return false;}catch{return true;}});
    return {initial:imported.item.source,round:f.parseFigure(JSON.stringify(doc)).items[0].source,legacy:f.parseFigure(old).items.length,report:imported.report,svg:imported.item.svg,invalid};
  });
  assert.deepEqual(result.initial,{name:'原始图.svg',kind:'svg'});assert.deepEqual(result.round,{name:'研究论文.pdf',kind:'pdf',page:3});assert.equal(result.legacy,0);assert.deepEqual(result.invalid,[true,true,true,true]);
  for(const tag of ['switch','foreignObject','feImage']){assert.ok(result.report.removedElements.includes(tag));assert.ok(result.report.warnings.some(warning=>warning.includes(tag)));}
  assert.match(result.svg,/feGaussianBlur/);assert.doesNotMatch(result.svg,/Conditional content|<switch|<foreignObject|<feImage/);
});
