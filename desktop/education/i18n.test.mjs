import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {after, before, test} from 'node:test';
import {createServer} from 'vite';
import {createSSRApp, computed} from 'vue';
import {createPinia} from 'pinia';
import {renderToString} from '@vue/server-renderer';
import {parse as parseSfc} from '@vue/compiler-sfc';
import {parse} from '@vue/compiler-dom';
const root=fileURLToPath(new URL('./',import.meta.url));
const names=['Journals','Recommend','Presubmit','Report','Format','Reformat','Rebuttal','Calendar','Ppt'];
let server,locale,useEduStore;
before(async()=>{
 globalThis.location=new URL('http://127.0.0.1:19999/');
 globalThis.localStorage={getItem:()=>null};
 server=await createServer({configFile:fileURLToPath(new URL('../vite.education.config.mjs',import.meta.url)),server:{middlewareMode:true},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 locale=await server.ssrLoadModule('/src/i18n.js');
 ({useEduStore}=await server.ssrLoadModule('/src/stores/edu.js'));
});
after(async()=>{await server?.close();});
function storeForTest(){
 const pinia=createPinia();const store=useEduStore(pinia);
 store.journalsStatus='ready';store.directoryStatus='ready';store.directoryMeta={lang:'en',total:0,domains:[],data_imported:true};
 return {store,pinia};
}
async function render(name,state,lang='en'){
 locale.setEducationLocale(lang);
 const {default:Component}=await server.ssrLoadModule(`/src/components/edu/panels/${name}Panel.vue`);
 return renderToString(createSSRApp(Component).use(state.pinia));
}
function assertEnglish(html,name){
 const visible=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<[^>]*>/g,'');
 assert.doesNotMatch(visible,/[\u3400-\u9fff]/u,`${name}: ${visible}`);
 assert.doesNotMatch(html,/(?:title|placeholder|aria-label|alt)="[^"]*[\u3400-\u9fff][^"]*"/u,`${name} accessibility/help text`);
}
test('every education panel renders all initial controls in English',async()=>{
 for(const name of names)assertEnglish(await render(name,storeForTest()),name);
});
test('all panel loading and error states are translated',async()=>{
 for(const name of names){
  for(const phase of ['loading','error']){
   const state=storeForTest();
   Object.assign(state.store,{directoryStatus:phase,directoryError:'网络异常，请检查连接',recommendStatus:phase,recommendError:'请求超时，请重试',reportStatus:phase,reportError:'服务异常，请稍后重试',formatStatus:phase,formatError:'PDF 已加密，无法解析',reformatStatus:phase,reformatError:'源码超过 2MB 上限',conferencesStatus:phase,conferencesError:'网络异常，请检查连接'});
   state.store.ppt.status=phase;state.store.ppt.error='正文过短（<300 字），无法生成汇报大纲';
   state.store.rebuttal.status=phase;state.store.rebuttal.error='请粘贴审稿意见文本';
   assertEnglish(await render(name,state),`${name}/${phase}`);
  }
 }
});
test('known backend formatting and reformatting messages remain informative in English',async()=>{
 const state=storeForTest();
 state.store.formatReport={ok:true,meta:{filename:'sample.pdf',file_type:'pdf',ruleset_source:'default',pages:12,refs_style_expected:'APA',refs_style_detected:'APA'},summary:{pass:1,warn:0,fail:1},checks:[{key:'pages',name:'页数',status:'fail',detail:'共 12 页，超出规则上限 10 页',suggestion:'压缩至 ≤10 页，或将方法/图表移入补充材料'}]};
 const html=await render('Format',state);assertEnglish(html,'Format/report');assert.match(html,/12 pages; exceeds the 10-page limit/);
 state.store.reformatResult={ok:true,job_id:'123456abcdef',modified_tex:'source text',changes:[{line:1,rule_key:'font_pt',action:'modified',original:'10pt',modified:'11pt',reason:'正文字号 10pt 低于目标下限 11pt，改为 11pt'}],warnings:[],meta:{filename:'sample.tex',journal_name:'Journal',rules_source:'default',rules:{min_line_spacing:1.5,min_font_pt:11,min_margin_mm:20,refs_style:'apa'}}};
 const reformatted=await render('Reformat',state);assertEnglish(reformatted,'Reformat/report');assert.match(reformatted,/Font size ≥ 11 pt/);
 assert.equal(locale.et('四边最小 18mm（上18、右20、下18、左20），规则下限 25mm'),'Smallest margin: 18 mm (Top 18, Right 20, Bottom 18, Left 20); required minimum: 25 mm');
 assert.equal(locale.et('主要字号 11pt（约 90%），共 2 种字体；高频字体：Arial(300字)、Times(50字)'),'Main font size: 11 pt (about 90%); 2 fonts; most frequent: Arial (300 characters), Times (50 characters)');
});
test('loaded review, journal, calendar, and presentation UI labels are English',async()=>{
 const state=storeForTest();
 state.store.report={ok:true,meta:{filename:'sample.pdf',text_chars:800,target_journal:'Journal',tier:2,tier_label:'主流期刊',dropped_sections:['references','acknowledgements']},report:{summary:'Manuscript summary',recommendation:'minor_revision',major_weaknesses:[],minor_weaknesses:[],presentation_format:[],questions_for_authors:[],recommendation_rationale:{tier:2,tier_label:'主流期刊',explanation:'无大修级问题，小修级 2 条 → 建议小修。',hard_hits:['BUSCO 完整度缺失'],scanner_conflicts:[]}}};
 for(const name of ['Presubmit','Report'])assertEnglish(await render(name,state),`${name}/report`);
 state.store.directoryMeta={lang:'en',total:1,domains:['基因组学','生物信息学'],data_imported:true};
 state.store.directory=[{id:'test',name:'Test Journal',research_direction:'生物信息学',cas_partition:2,jif:4,official_url:'https://example.org'}];
 assertEnglish(await render('Journals',state),'Journals/data');
 state.store.conferencesStatus='ready';state.store.conferences=[{id:'test',name:'Test Conference',full_name:'Research Conference',domain_tags:['基因组学','生物信息学'],application_tags:['交叉学科'],deadline_source:'placeholder',deadline_confidence:'low',deadline_full_paper:'2028-01-01T12:00:00Z',location:'Test City'}];
 assertEnglish(await render('Calendar',state),'Calendar/data');
 state.store.ppt.status='editing';state.store.ppt.job={id:'123456abcdef',scenario:'group_meeting',style_id:'academic-minimal',minutes:15,pages:[{slide_number:1,type:'title',title:'Presentation title',bullets:[],evidence:[],suggested_figures:[]}],warnings:[]};
 assertEnglish(await render('Ppt',state),'Ppt/editing');
});
test('language switches reactively without modifying drafts, results, filenames or source',async()=>{
 const state=storeForTest();state.store.drafts.abstract='用户填写的论文摘要：保存';
 const label=computed(()=>locale.et('审稿回复助手'));
 locale.setEducationLocale('zh');assert.equal(label.value,'审稿回复助手');
 locale.setEducationLocale('en');assert.equal(label.value,'Reviewer response assistant');
 const original=JSON.stringify(state.store.$state);
 const english=await render('Recommend',state,'en');assert.match(english,/用户填写的论文摘要：保存/);
 const chinese=await render('Recommend',state,'zh');assert.match(chinese,/智能投稿推荐/);
 assert.equal(JSON.stringify(state.store.$state),original);
 assert.equal(locale.setEducationLocale('invalid'),false);
});
test('parent handshake rejects unrelated sources and origins',()=>{
 const host={};const other={};const valid={source:host,origin:'http://tauri.localhost'};
 assert.equal(locale.isTrustedHostMessage(valid,host,''),true);
 assert.equal(locale.isTrustedHostMessage({...valid,source:other},host,''),false);
 assert.equal(locale.isTrustedHostMessage({...valid,origin:'https://example.org'},host,''),false);
 assert.equal(locale.isTrustedHostMessage(valid,host,'http://tauri.localhost/settings'),true);
 assert.equal(locale.isTrustedHostMessage(valid,host,'https://example.org/'),false);
 assert.equal(locale.isTrustedHostMessage({...valid,origin:'tauri://localhost'},host,'tauri://localhost/'),true);
});
test('Vue templates have no unbound authored Chinese labels or help text',()=>{
 const files=[path.join(root,'src/components/edu/EduSideNav.vue'),...names.map(n=>path.join(root,`src/components/edu/panels/${n}Panel.vue`))];
 for(const file of files){const {descriptor}=parseSfc(fs.readFileSync(file,'utf8'));const ast=parse(descriptor.template.content);
  function visit(n){if(n.type===2)assert.doesNotMatch(n.content,/[\u3400-\u9fff]/,`${file}: ${n.content}`);for(const p of n.props||[])if(p.type===6&&['title','placeholder','label','alt','aria-label'].includes(p.name))assert.doesNotMatch(p.value?.content||'',/[\u3400-\u9fff]/,`${file}: ${p.name}`);for(const c of n.children||[])visit(c);}visit(ast);
 }
});
