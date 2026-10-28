import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Small DOM fixture for the two native-view decorators; no browser, server, or model is started.
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.attrs={};this.dataset={};this.style={};this.events=new Map();this.value='';this._text='';}
  get className(){return this.attrs.class||'';} set className(value){this.attrs.class=value;}
  get classList(){return {contains:value=>this.className.split(' ').includes(value)};}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  set textContent(value){this._text=value;this.children=[];}
  set innerHTML(value){this.children=[];for(const match of value.matchAll(/<span class="([^"]+)">([^<]*)<\/span>/g)){const span=new Element('span');span.className=match[1];span.textContent=match[2];this.append(span);}}
  get nextElementSibling(){return this.parent?.children[this.parent.children.indexOf(this)+1]||null;}
  get previousElementSibling(){return this.parent?.children[this.parent.children.indexOf(this)-1]||null;}
  setAttribute(key,value){this.attrs[key]=String(value);}
  getAttribute(key){return this.attrs[key]??null;}
  hasAttribute(key){return key in this.attrs;}
  removeAttribute(key){delete this.attrs[key];}
  append(...children){for(const child of children){child.remove();child.parent=this;this.children.push(child);}}
  appendChild(child){this.append(child);return child;}
  insertBefore(child,anchor){child.remove();child.parent=this;this.children.splice(this.children.indexOf(anchor),0,child);}
  before(child){this.parent.insertBefore(child,this);}
  remove(){if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}}
  replaceChildren(...children){for(const child of [...this.children])child.remove();this._text='';this.append(...children);}
  addEventListener(name,callback){this.events.set(name,callback);}
  matches(selector){
    if(selector.includes(' '))return false;
    const tag=selector.match(/^[a-z]+/);if(tag&&this.tagName!==tag[0])return false;
    const cls=selector.match(/^\.([\w-]+)/);if(cls&&!this.classList.contains(cls[1]))return false;
    for(const match of selector.matchAll(/\[([\w-]+)(?:([*$]?=)"([^"]*)")?\]/g)){
      const value=match[1].startsWith('data-') ? this.attrs[match[1]]??this.dataset[match[1].slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())] : this.attrs[match[1]];
      if(value===undefined)return false;
      if(match[2]==='='&&value!==match[3])return false;
      if(match[2]==='*='&&!value.includes(match[3]))return false;
      if(match[2]==='$='&&!value.endsWith(match[3]))return false;
    }
    return !!(tag||cls||selector.includes('['));
  }
  querySelectorAll(selector){return this.children.flatMap(child=>[...(selector.split(',').some(value=>child.matches(value.trim()))?[child]:[]),...child.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  closest(selector){return selector.split(',').some(value=>this.matches(value.trim()))?this:this.parent?.closest(selector)||null;}
}
function fixture(source,earlyDocument=false){
  const events=new Map(),frames=[],head=new Element('head'),body=new Element('body'),document={head,body,readyState:'complete',documentElement:{lang:'en'},createElement:tag=>new Element(tag),querySelector:s=>body.querySelector(s),querySelectorAll:s=>body.querySelectorAll(s)};
  const location={origin:'http://fixture.invalid',href:''},window={location,addEventListener:(name,callback)=>events.set(name,callback)};
  const context=vm.createContext({document,window,location,navigator:{language:'en-US'},MutationObserver:class{observe(){}},requestAnimationFrame:callback=>frames.push(callback),setTimeout:callback=>frames.push(callback),CSS:{escape:value=>value},console});
  if(earlyDocument){document.documentElement=null;document.readyState='loading';document.addEventListener=(name,callback)=>events.set('document:'+name,callback);}
  return {body,document,window,start:()=>vm.runInContext(source,context),ready(language){document.documentElement={lang:language};document.readyState='complete';events.get('document:DOMContentLoaded')();this.flush();},flush(){for(let i=0;frames.length&&i<20;i++)frames.shift()();assert.equal(frames.length,0);},send(detail){events.get('omaax-reasoning')({detail});this.flush();}};
}
const read=file=>fs.readFileSync(new URL('../src-tauri/src/'+file,import.meta.url),'utf8');

test('native document-created injection waits for HTML before applying branding and conversation decorators',()=>{
  const source=read('lib.rs').match(/const DSH_INIT_SCRIPT: &str = r#"([\s\S]*?)"#;/)?.[1];assert.ok(source);
  const h=fixture(source,true),frame=new Element(),column=new Element();frame.className='fixture_frame';frame.style.gridTemplateColumns='264px 1fr';column.className='fixture_column';frame.append(column);h.body.append(frame);
  for(const kind of ['user','assistant-step','tool-call','turn-tail']){const item=new Element();item.setAttribute('data-chat-flow-kind',kind);column.append(item);}
  h.start();assert.equal(h.document.head.children.length,0);
  h.ready('zh');assert.match(h.document.head.children[0].textContent,/\[class\*="_headline"\]/);
  assert.equal(frame.style.gridTemplateColumns,'0px 1fr');
  assert.match(column.querySelector('.biodsh-txt').textContent,/OMaaX 完成了 2 步/);
  h.send({language:'en'});assert.match(column.querySelector('.biodsh-txt').textContent,/OMaaX completed 2 steps/);
});

test('native reasoning controls switch language without changing mode IDs or the selected mode',()=>{
  const h=fixture(read('chat_composer.js')),card=new Element(),target=new Element();card.setAttribute('data-composer-card','');target.className='fixture_modes';card.append(target);h.body.append(card);h.start();
  const state={sessionId:'session with spaces',levels:['low','high','max'],selected:'max',language:'en'};h.send(state);
  const select=target.querySelector('select');assert.equal(target.querySelector('span').textContent,'Reasoning mode');assert.deepEqual(select.children.map(option=>[option.value,option.textContent]),[['low','Low'],['high','Medium'],['max','High']]);assert.equal(select.value,'max');
  h.send({...state,language:'zh'});assert.equal(target.querySelector('span').textContent,'推理模式');assert.equal(select.children[2].textContent,'高');assert.equal(select.value,'max');
  h.send({...state,busy:true});assert.equal(select.getAttribute('aria-label'),'Reasoning mode');assert.equal(select.disabled,true);assert.equal(select.children[2].textContent,'High');
  h.send({...state,error:'Reasoning mode is temporarily unavailable'});assert.equal(target.querySelector('label').title,'Reasoning mode is temporarily unavailable');
  h.send(state);select.value='low';select.events.get('change')({stopPropagation(){}});assert.equal(h.window.location.href,'http://fixture.invalid/__omaax_mode?session=session%20with%20spaces&effort=low');
  h.send({sessionId:null,language:'en'});assert.equal(target.querySelector('label'),null);
});

test('native conversation summaries and legacy handoff notices change language while research content is preserved',()=>{
  const source=read('lib.rs').match(/const DSH_INIT_SCRIPT: &str = r#"([\s\S]*?)"#;/)?.[1];assert.ok(source);
  const h=fixture(source),frame=new Element(),column=new Element();frame.className='fixture_frame';frame.style.gridTemplateColumns='264px 1fr';column.className='fixture_column';frame.append(column);h.body.append(frame);
  const item=(kind,text)=>{const el=new Element();el.setAttribute('data-chat-flow-kind',kind);el.textContent=text;column.append(el);return el;};
  const user=item('user','我的植物课题'),think=item('assistant-step','研究推理内容'),tool=item('tool-call','输入数据.csv'),final=item('assistant-step',''),markdown=new Element();markdown.className='fixture_markdown_content';markdown.textContent='中文研究结果';final.append(markdown);const tail=item('turn-tail','');
  h.start();h.flush();assert.equal(column.querySelector('.biodsh-txt').textContent,'OMaaX completed 2 steps (1 reasoning · 1 actions) · View details');assert.equal(user.textContent,'我的植物课题');assert.equal(markdown.textContent,'中文研究结果');
  h.send({language:'zh'});assert.match(column.querySelector('.biodsh-txt').textContent,/OMaaX 完成了 2 步/);
  final.remove();tail.remove();const running=new Element();running.setAttribute('data-state','running');h.body.append(running);h.send({language:'en'});assert.equal(column.querySelector('.biodsh-txt').textContent,'OMaaX is working. Please wait…');assert.equal(column.querySelector('.biodsh-n').textContent,'2 steps');
  const original='用户在服务器管理中选择了以下 SSH 服务器：测试主机 mcp__biodsh_ssh__list_servers';user.textContent=original;h.send({language:'zh'});assert.equal(column.querySelector('.omaax-handoff').textContent,'已选择服务器，请查看可用资源并协助后续分析。');h.send({language:'en'});assert.equal(column.querySelector('.omaax-handoff').textContent,'Server selected. Check available resources and continue the analysis.');assert.equal(user.textContent,original);assert.equal(think.textContent,'研究推理内容');assert.equal(tool.textContent,'输入数据.csv');
});

test('slot-wrapped and grouped history keeps the newer client in charge of disclosures',()=>{
  const source=read('lib.rs').match(/const DSH_INIT_SCRIPT: &str = r#"([\s\S]*?)"#;/)?.[1];
  const h=fixture(source),frame=new Element(),column=new Element(),slot=new Element(),group=new Element();
  frame.className='real_frame';frame.style.gridTemplateColumns='264px 1fr';column.className='real_column';column.setAttribute('data-chat-flow','');slot.setAttribute('data-slot','conversation.chat.flow');group.setAttribute('data-chat-group-key','historical-turn');
  frame.append(column);column.append(slot);slot.append(group);h.body.append(frame);
  const item=(kind,text)=>{const el=new Element();el.setAttribute('data-chat-flow-kind',kind);el.textContent=text;group.append(el);return el;};
  const user=item('user','研究问题'),process=item('turn-process','上游折叠按钮'),think=item('assistant-step','分析过程'),tool=item('tool-call','读取文件'),answer=item('assistant-step',''),tail=item('turn-tail','');
  think.setAttribute('data-turn-process-member','true');think.setAttribute('data-turn-process-hidden','true');
  for(const el of [think,tool,answer]){el.setAttribute('data-biodsh-hidden','1');el.setAttribute('data-biodsh-mid','1');}
  answer.setAttribute('data-biodsh-open','0');
  const md=new Element();md.className='_markdown_1ypvv_5';md.textContent='最终科研回答';answer.append(md);
  const oldSummary=new Element();oldSummary.className='biodsh-sum';column.append(oldSummary);
  h.start();h.flush();
  assert.equal(column.querySelector('.biodsh-sum'),null);
  for(const el of [think,tool,answer])assert.equal(el.getAttribute('data-biodsh-hidden'),null);
  assert.equal(think.getAttribute('data-turn-process-hidden'),'true');
  assert.equal(answer.getAttribute('data-biodsh-open'),null);
  assert.equal(answer.getAttribute('data-biodsh-final'),'1');
  assert.equal(md.textContent,'最终科研回答');assert.equal(process.textContent,'上游折叠按钮');
  h.send({language:'zh'});assert.equal(user.textContent,'研究问题');assert.equal(column.querySelector('.biodsh-sum'),null);
  const legacy='用户在服务器管理中选择了以下 SSH 服务器：测试主机 mcp__biodsh_ssh__list_servers';
  user.textContent=legacy;h.send({language:'en'});assert.match(group.querySelector('.omaax-handoff').textContent,/Server selected/);
  h.send({language:'zh'});assert.match(group.querySelector('.omaax-handoff').textContent,/已选择服务器/);assert.equal(user.textContent,legacy);
});
