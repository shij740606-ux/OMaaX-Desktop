import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {root,React,act,settle,text,fixture,assertEnglish} from './locale-test-utils.mjs';
const {create}=createRequire(import.meta.url)('react-test-renderer');
const read=file=>JSON.parse(fs.readFileSync(root+file,'utf8'));

test('science, knowledge, and resource interfaces switch both ways without translating research content',async()=>{
 const database=read('resources/databases/catalog.json');
 const cases=[['views/DrawingView.tsx',{active:false}],['views/MolecularView.tsx',{active:false}],['views/CloudKnowledgeView.tsx',{}],['views/KnowledgeView.tsx',{}],['views/DataView.tsx',{}],['views/DatabaseView.tsx',{}]];
 for(const [file,props] of cases){
  const h=fixture({api:{cloudQa:async()=>[],knowledgeCredential:async()=>({configured:true}),workspaceFiles:async()=>[],dbCatalog:async()=>({catalog:database,vectors:null})}}),View=h.get(file).default;let view;
  await act(async()=>{view=create(h.wrap(View,'en',props));await settle();});assertEnglish(view);
  await act(async()=>view.update(h.wrap(View,'zh',props)));assert.match(text(view.toJSON()),/[\p{Script=Han}]/u,file);
  await act(async()=>view.update(h.wrap(View,'en',props)));assertEnglish(view);await act(async()=>view.unmount());
 }
});

test('all built-in database metadata and tool category labels have English translations',()=>{
 const {translate}=fixture().get('i18n.ts'),catalog=read('resources/databases/catalog.json');
 const strings=[];for(const c of catalog.categories){strings.push(c.name,c.desc);for(const d of c.databases)strings.push(d.name,d.desc,...d.tags||[],...d.examples||[]);}
 for(const item of Object.values(read('store/community-zh.json').items))strings.push(item.domain,item.subcategory,item.level);
 for(const value of strings.filter(Boolean))assert.doesNotMatch(translate('en',value),/[\p{Script=Han}]/u,value);
 for(const tool of read('store/community-index.json').skills){assert.doesNotMatch(translate('en',tool.name),/[\p{Script=Han}]/u,tool.id);assert.doesNotMatch(translate('en',tool.description),/[\p{Script=Han}]/u,tool.id);if(tool.sourceGuide)assert.doesNotMatch(translate('en',tool.sourceGuide.requirements),/[\p{Script=Han}]/u,tool.id);}
 for(const tool of Object.values(read('store/skill-meta.json')))for(const value of [tool.title,tool.summary,tool.score_source,...tool.tags||[],...tool.benchmarks||[]].filter(Boolean))assert.doesNotMatch(translate('en',value),/[\p{Script=Han}]/u,value);
});

test('software-generated search status follows language while user questions and source text remain unchanged',()=>{
 const h=fixture(),{messageBody,messageText}=h.get('views/CloudKnowledgeView.tsx');
 const message={role:'assistant',text:'找到 2 条文献来源。',blocks:[{type:'sources',items:[{title:'原文标题',snippet:'原文摘要'}]}]};
 assert.equal(messageBody(message,'en'),'Found 2 literature sources.');assert.equal(messageBody(message,'zh'),'找到 2 条文献来源。');
 assert.equal(messageBody({...message,role:'user'},'en'),'找到 2 条文献来源。');assert.match(messageText(message,'en'),/原文标题\n原文摘要/);
 assert.equal(messageBody({...message,text:'研究内容不应改写'},'en'),'研究内容不应改写');
});

test('asset names are English while category selections retain stable values and drawing labels remain untouched',async()=>{
 const h=fixture(),View=h.get('views/DrawingAssets.tsx').default,assets=h.get('views/drawing-model.ts').assets;let view;
 const diagram={title:'我的课题',nodes:[{id:'x',text:'实验标签',x:0,y:0,color:'#ffffff',shape:'rectangle'}],edges:[]};
 const props={diagram,selection:[],onSelect:()=>{},onAdd:()=>{},onChange:()=>{},disabled:false};
 await act(async()=>{view=create(h.wrap(View,'en',props));});assertEnglish(view,['实验标签']);
 assert.ok(assets.length>=148);for(const a of assets)assert.doesNotMatch(a.name_en,/[\p{Script=Han}]/u,a.id);
 const select=view.root.findByType('select'),first=select.findAllByType('option')[1];const value=first.props.value;await act(async()=>select.props.onChange({target:{value}}));
 await act(async()=>view.update(h.wrap(View,'zh',props)));assert.equal(view.root.findByType('select').props.value,value);
 await act(async()=>view.update(h.wrap(View,'en',props)));assertEnglish(view,['实验标签']);assert.equal(diagram.nodes[0].text,'实验标签');await act(async()=>view.unmount());
});

test('knowledge errors change language live and cancellation keeps its state classification',async()=>{
 const h=fixture({api:{cloudQa:async()=>{throw Error('fixture');}}}),View=h.get('views/CloudKnowledgeView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View,'zh'));await settle();});assert.match(text(view.toJSON()),/会话列表暂时无法加载/);
 await act(async()=>view.update(h.wrap(View,'en')));assertEnglish(view);assert.match(text(view.toJSON()),/Unable to load conversations/);
 await act(async()=>view.update(h.wrap(View,'zh')));assert.match(text(view.toJSON()),/会话列表暂时无法加载/);await act(async()=>view.unmount());
});

test('tool catalog English mode translates recovered-source metadata, badges, and filters',async()=>{
 const source=read('store/community-index.json').skills.find(s=>s.id==='plant-sommer-mixed-models')||read('store/community-index.json').skills.find(s=>s.name.includes('混合模型与遗传力'));
 assert.ok(source);const skill={...source,name:'混合模型与遗传力估计',name_en:source.name,summary:source.description,description:source.description,domain_zh:'遗传育种与性状定位',subcategory:'遗传力与亲缘矩阵',level:'进阶',tier:'community',tags:[],inputs:[],outputs:[],offline:false,mutates_input:false,featured:true};
 const h=fixture({state:{catalog:[skill],env:{ready:true},statuses:{}},api:{ratingsGet:async()=>({})}}),View=h.get('views/StoreView.tsx').default;let view;
 await act(async()=>{view=create(h.wrap(View));await settle();});assertEnglish(view);
 await act(async()=>view.root.findAllByType('button').find(n=>text(n.toJSON?.()||n).includes('Extended tools')).props.onClick());assertEnglish(view);
 await act(async()=>view.update(h.wrap(View,'zh')));assert.match(text(view.toJSON()),/扩展工具/);await act(async()=>view.unmount());
});

test('drawing open and export actions forward the current language to native dialogs without rewriting diagram text',async()=>{
 const calls=[],saved={format:'omaax-drawing',version:1,diagram:{title:'我的课题',nodes:[{id:'n',text:'原始实验标签',x:0,y:0,color:'#ffffff',shape:'rectangle'}],edges:[]}};
 const h=fixture({api:{drawing:async(action,body)=>{calls.push({action,body});return action==='open'?saved:null;}}}),View=h.get('views/DrawingView.tsx').default,{translate}=h.get('i18n.ts');let view;
 const click=async(key,lang)=>{const button=view.root.findAllByType('button').find(button=>text(button)===translate(lang,key));assert.ok(button,key);await act(async()=>{button.props.onClick();await settle();});};
 await act(async()=>{view=create(h.wrap(View,'en',{active:false}));await settle();});
 await click('打开绘图','en');assert.equal(calls.at(-1).body.lang,'en');await click('导出 SVG','en');assert.equal(calls.at(-1).body.lang,'en');assert.match(calls.at(-1).body.svg,/原始实验标签/);
 await act(async()=>view.update(h.wrap(View,'zh',{active:false})));await click('导出 SVG','zh');assert.equal(calls.at(-1).body.lang,'zh');assert.match(calls.at(-1).body.svg,/我的课题/);
 await click('打开分子绘图','zh');assert.equal(calls.at(-1).body.lang,'zh');
 await act(async()=>view.update(h.wrap(View,'en',{active:false})));await click('打开分子绘图','en');assert.equal(calls.at(-1).body.lang,'en');await act(async()=>view.unmount());
});

test('older tool metadata with missing display fields remains searchable and keeps its category across language changes',async()=>{
 const skill={...read('resources/skills/catalog.json').skills.find(s=>s.tier==='community'),id:'legacy-fixture-tool',name:undefined,name_en:undefined,summary:undefined,description:undefined,tags:undefined,domain_zh:undefined,featured:true};
 const h=fixture({state:{catalog:[skill],env:{ready:true},statuses:{}},api:{ratingsGet:async()=>({})}}),View=h.get('views/StoreView.tsx').default,{translate}=h.get('i18n.ts');let view;
 await act(async()=>{view=create(h.wrap(View,'en'));await settle();});assertEnglish(view);assert.match(text(view.toJSON()),/legacy-fixture-tool/);
 await act(async()=>view.root.findAllByType('button').find(button=>text(button).startsWith('Extended tools')).props.onClick());
 await act(async()=>view.root.findAllByType('button').find(button=>text(button).includes(translate('en','数据与工具'))).props.onClick());
 await act(async()=>view.root.findByProps({'aria-label':translate('en','搜索工具')}).props.onChange({target:{value:'legacy-fixture'}}));assert.match(text(view.toJSON()),/legacy-fixture-tool/);
 await act(async()=>view.update(h.wrap(View,'zh')));assert.match(text(view.toJSON()),/legacy-fixture-tool/);
 await act(async()=>view.update(h.wrap(View,'en')));assertEnglish(view);assert.match(text(view.toJSON()),/legacy-fixture-tool/);await act(async()=>view.unmount());
});

test('production tool evaluation fields are translated in cards and details without changing dataset identifiers',async()=>{
 const catalog=read('resources/skills/catalog.json').skills.filter(skill=>skill.evidence);
 const h=fixture({state:{catalog,env:{ready:true},statuses:{}},api:{ratingsGet:async()=>({}),skillReadme:async()=>''}}),View=h.get('views/StoreView.tsx').default,{translate}=h.get('i18n.ts');let view;
 await act(async()=>{view=create(h.wrap(View,'en'));await settle();});assertEnglish(view);
 for(const skill of catalog.filter(skill=>skill.evidence.tests)){
  const name=translate('en',skill.name_en||skill.name);
  const card=view.root.findAll(n=>typeof n.type==='string'&&n.props.className?.includes('omaax-tool-card')).find(n=>text(n).includes(name));assert.ok(card,skill.id);
  await act(async()=>{card.props.onClick();await settle();});assertEnglish(view);
  assert.match(text(view.toJSON()),new RegExp(translate('en',skill.evidence.tests).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  if(skill.evidence.dataset)assert.ok(text(view.toJSON()).includes(skill.evidence.dataset));
  await act(async()=>view.root.findByProps({className:'sheet-backdrop'}).props.onClick());
 }
 await act(async()=>view.update(h.wrap(View,'zh')));assert.match(text(view.toJSON()),/往返读写测试/);
 await act(async()=>view.update(h.wrap(View,'en')));assertEnglish(view);await act(async()=>view.unmount());
});
