"""Browser acceptance: real React views, deterministic cloud bridge, no live QA load."""
import json,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
BASE=os.environ.get('OMAAX_QA_URL','http://127.0.0.1:41740').rstrip('/')
out=ROOT/'validation-local/cloud-browser';out.mkdir(exist_ok=True)
checks=[];errors=[];external=[]
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
 page=browser.new_page(viewport={'width':1280,'height':900});page.on('pageerror',lambda e:errors.append(str(e)))
 page.route('**/*',lambda route:route.continue_() if route.request.url.startswith((BASE+'/',BASE.replace('http:','ws:')+'/', 'data:','blob:')) else (external.append(route.request.url),route.abort()))
 try:
  page.goto(BASE+'/workflow-preview.html?auth=qa&cloud=1');page.wait_for_load_state('networkidle')
  page.get_by_role('button',name='知识库问答',exact=True).click()
  expect(page.get_by_role('heading',name='文献知识库问答')).to_be_visible()
  expect(page.get_by_role('button',name='分析结果',exact=True)).to_be_visible()
  expect(page.get_by_role('button',name='教育科研',exact=True)).to_be_visible()
  checks.append('ordinary account with qa can open knowledge and standard analysis/research')
  q=page.get_by_label('科研问题',exact=True);q.fill('水稻 SPL 功能');page.get_by_role('button',name='提问',exact=True).click()
  expect(page.get_by_text('水稻 SPL 参与株型与开花调控。[DOI:10.1/test]',exact=True)).to_be_visible()
  page.get_by_text('参考文献（2）',exact=True).click();expect(page.get_by_text('研究证据摘要')).to_be_visible()
  expect(page.get_by_role('button',name='查看来源',exact=True)).to_have_count(1)
  page.get_by_role('button',name='查看来源').click();assert page.evaluate('window.__openedSource')=='https://doi.org/10.1/test'
  checks.append('answer and evidence shown; safe source link works; javascript link suppressed')
  sid=page.evaluate('window.__cloudQa.sessions[0].id')
  page.get_by_role('button',name='新建问答').click();expect(page.get_by_text('水稻 SPL 参与株型与开花调控。[DOI:10.1/test]',exact=True)).to_have_count(0)
  expect(q).to_be_focused()
  assert page.evaluate('window.__cloudQa.sessions.length')==2
  new_id=page.get_by_label('知识库会话').input_value();assert new_id and new_id!=sid
  page.evaluate("window.__cloudQa.failure='create'")
  page.get_by_role('button',name='新建问答').click();expect(page.get_by_role('alert')).to_contain_text('新建问答失败')
  expect(page.get_by_label('知识库会话')).to_have_value(new_id)
  page.evaluate("window.__cloudQa.failure=''")
  checks.append('new session is created immediately, focused, and failure preserves current session')
  page.get_by_label('知识库会话').select_option(sid)
  expect(page.get_by_text('水稻 SPL 参与株型与开花调控。[DOI:10.1/test]',exact=True)).to_be_visible()
  checks.append('saved messages and citation blocks restore from selected session')
  page.get_by_role('button',name='删除会话').click();expect(page.get_by_role('alert')).to_contain_text('已取消删除');assert page.evaluate('window.__cloudQa.sessions.length')==2
  page.evaluate('window.__cloudQa.deleteAllowed=true');page.get_by_role('button',name='删除会话').click();expect(page.get_by_label('知识库会话')).to_have_value('');assert page.evaluate('window.__cloudQa.sessions.length')==1
  checks.append('cancelled deletion preserves history; confirmed deletion clears selected session')
  q.fill('文献检索');page.get_by_role('button',name='仅检索文献').click();expect(page.get_by_text('找到 2 条文献来源。',exact=True)).to_be_visible()
  checks.append('search-only mode returns evidence')
  page.evaluate("window.__cloudQa.failure='chat'");q.fill('服务故障');page.get_by_role('button',name='提问',exact=True).click();expect(page.get_by_role('alert')).to_contain_text('暂不可用');expect(q).to_be_enabled()
  page.evaluate("window.__cloudQa.failure='save'");q.fill('保存失败');page.get_by_role('button',name='提问',exact=True).click();expect(page.get_by_role('status')).to_contain_text('回答已显示，但保存失败')
  checks.append('upstream failure permits retry; save failure preserves visible answer and warns')
  expect(q).to_be_disabled();expect(page.get_by_role('button',name='新建问答')).to_be_disabled()
  calls=page.evaluate("window.__cloudQa.calls.filter(c=>c==='chat').length")
  page.get_by_role('button',name='重试保存').click();expect(page.get_by_role('alert')).to_contain_text('保存仍未完成')
  page.evaluate("window.__cloudQa.failure=''");page.get_by_role('button',name='重试保存').click();expect(q).to_be_enabled()
  assert page.evaluate("window.__cloudQa.calls.filter(c=>c==='chat').length")==calls
  checks.append('save retry preserves answer, blocks session changes and never repeats model request')
  page.evaluate("window.__cloudQa.failure='';window.__cloudQa.delay=1200");q.fill('重复点击');page.get_by_role('button',name='提问',exact=True).click();expect(q).to_be_disabled();expect(page.get_by_role('button',name='新建问答')).to_be_disabled();expect(q).to_be_enabled(timeout=5000)
  checks.append('in-flight requests prevent duplicate sends and session changes')
  q.fill('切换页面后继续保存');page.get_by_role('button',name='提问',exact=True).click()
  page.get_by_role('button',name='设置',exact=True).click()
  expect(page.get_by_role('heading',name='文献知识库问答')).not_to_be_visible()
  page.wait_for_function("window.__cloudQa.sessions.some(s=>s.messages?.filter(m=>m.role==='assistant').length>=4)")
  page.get_by_role('button',name='知识库问答',exact=True).click();expect(q).to_be_enabled(timeout=5000)
  assert page.evaluate("window.__cloudQa.sessions.some(s=>s.messages?.some(m=>m.text==='切换页面后继续保存'))")
  checks.append('navigation keeps the active question running and saved')
  page.evaluate("""()=>{const original=window.biodsh.cloudQa;window.__cloudOriginal=original;window.biodsh.cloudQa=async(a,b)=>a==='list'?{sessions:{bad:true}}:original(a,b)}""")
  page.get_by_role('button',name='刷新会话').click();expect(page.get_by_role('alert')).to_contain_text('会话列表暂时无法加载')
  expect(page.get_by_role('heading',name='文献知识库问答')).to_be_visible()
  page.evaluate("window.biodsh.cloudQa=async(a,b)=>a==='chat'?{answer:{bad:true},sources:[]}:window.__cloudOriginal(a,b)")
  q.fill('异常答案');page.get_by_role('button',name='提问',exact=True).click();expect(page.get_by_role('alert')).to_contain_text('数据不完整');expect(q).to_have_value('异常答案')
  page.evaluate("window.biodsh.cloudQa=async(a,b)=>a==='chat'?{answer:'ok',sources:[null]}:window.__cloudOriginal(a,b)")
  page.get_by_role('button',name='提问',exact=True).click();expect(page.get_by_role('alert')).to_contain_text('数据不完整');expect(q).to_be_enabled()
  page.evaluate("window.biodsh.cloudQa=async(a,b)=>a==='detail'?{messages:[{role:'assistant',text:'ok',blocks:{bad:true}}]}:window.__cloudOriginal(a,b)")
  page.get_by_label('知识库会话').select_option(page.evaluate('window.__cloudQa.sessions[0].id'));expect(page.get_by_role('alert')).to_contain_text('会话加载失败')
  page.evaluate('window.biodsh.cloudQa=window.__cloudOriginal')
  checks.append('malformed list, answer, citations and history produce recoverable errors without crashing')
  page.evaluate("""()=>{window.biodsh.cloudQa=async(a,b)=>{const result=await window.__cloudOriginal(a,b);if(a==='message'&&b.role==='assistant')throw Error('response lost');return result}}""")
  q.fill('保存成功但响应丢失');page.get_by_role('button',name='提问',exact=True).click();expect(page.get_by_role('button',name='重试保存')).to_be_visible()
  before=page.evaluate('window.__cloudQa.sessions.reduce((n,s)=>n+s.messages.length,0)');page.evaluate('window.biodsh.cloudQa=window.__cloudOriginal')
  page.get_by_role('button',name='重试保存').click();expect(q).to_be_enabled();assert page.evaluate('window.__cloudQa.sessions.reduce((n,s)=>n+s.messages.length,0)')==before
  checks.append('lost save response reconciles history without duplicating the saved answer')
  page.evaluate("""()=>{window.__listResolvers=[];window.biodsh.cloudQa=(a,b)=>a==='list'?new Promise(resolve=>window.__listResolvers.push(resolve)):window.__cloudOriginal(a,b)}""")
  page.get_by_role('button',name='刷新会话').click();page.get_by_role('button',name='刷新会话').click()
  page.evaluate("window.__listResolvers[1]([...window.__cloudQa.sessions,{id:'new-row',title:'最新会话列表'}])")
  expect(page.get_by_role('option',name='最新会话列表')).to_have_count(1)
  page.evaluate("window.__listResolvers[0]([{id:'old-row',title:'过期会话列表'}])")
  expect(page.get_by_role('option',name='最新会话列表')).to_have_count(1);expect(page.get_by_role('option',name='过期会话列表')).to_have_count(0)
  page.evaluate('window.biodsh.cloudQa=window.__cloudOriginal');page.get_by_role('button',name='刷新会话').click()
  checks.append('out-of-order refresh cannot replace the latest session list')
  page.evaluate("window.__cloudQa.failure='message'");calls=page.evaluate("window.__cloudQa.calls.filter(c=>c==='chat').length")
  q.fill('问题尚未保存');page.get_by_role('button',name='提问',exact=True).click();expect(q).to_be_enabled();expect(page.get_by_role('alert')).to_be_visible()
  assert page.evaluate("window.__cloudQa.calls.filter(c=>c==='chat').length")==calls
  page.evaluate("window.__cloudQa.failure='save'");q.fill('放弃保存确认');page.get_by_role('button',name='提问',exact=True).click();expect(page.get_by_role('button',name='放弃保存')).to_be_visible()
  page.once('dialog',lambda d:d.dismiss());page.get_by_role('button',name='放弃保存').click();expect(q).to_be_disabled()
  page.once('dialog',lambda d:d.accept());page.get_by_role('button',name='放弃保存').click();expect(q).to_be_enabled();expect(page.get_by_role('status')).to_have_count(0)
  checks.append('question-save failure avoids model call; discarding unsaved answer requires confirmation')
  page.evaluate(r"""()=>{window.__cloudQa.failure='';window.__imageCalls=0;window.__badExecuted=false;window.biodsh.cloudQa=async(a,b)=>{
   if(a==='chat')return {answer:'# 排版测试\n\n**重点**\n\n|名称|结果|\n|---|---|\n|SPL|证据|\n\n![文献示意图](/api/figures/proxy?pmcid=PMC1&url=https%3A%2F%2Fexample.org%2Fa.png&sig=test)\n\n<script>window.__badExecuted=true</script>\n\n[恶意链接](javascript:alert(1))',sources:[]};
   if(a==='image'){window.__imageCalls++;if(window.__imageFail)throw Error('403');return {src:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jf1sAAAAASUVORK5CYII='};}
   return window.__cloudOriginal(a,b);
  }}""")
  q.fill('图片与排版测试');page.get_by_role('button',name='提问',exact=True).click()
  expect(page.get_by_role('heading',name='排版测试')).to_be_visible();expect(page.get_by_role('table')).to_be_visible()
  img=page.get_by_role('img',name='文献示意图');expect(img).to_be_visible()
  page.wait_for_function("document.querySelector('img[alt=文献示意图]').naturalWidth>0")
  assert not page.evaluate('window.__badExecuted');assert page.locator('a[href^="javascript:"]').count()==0
  assert page.evaluate('window.__imageCalls')==1
  image_sid=page.get_by_label('知识库会话').input_value()
  page.get_by_role('button',name='新建问答').click();expect(img).to_have_count(0)
  page.evaluate('window.__imageFail=true');page.get_by_label('知识库会话').select_option(image_sid)
  expect(page.get_by_role('button',name='重新加载图片')).to_be_visible()
  page.evaluate('window.__imageFail=false');page.get_by_role('button',name='重新加载图片').click()
  expect(img).to_be_visible();page.wait_for_function("document.querySelector('img[alt=文献示意图]').naturalWidth>0")
  checks.append('Markdown headings tables and images render; unsafe HTML blocked; restored images retry after failure')
  page.evaluate("""()=>{const s=window.__cloudQa.sessions.find(s=>s.id===document.querySelector('[aria-label=知识库会话]').value);const m=s.messages.at(-1);m.blocks=[{type:'text',html:'<div data-omaax-markdown="1">'+m.text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')+'</div>'},...(m.blocks||[])];m.text=null;}""")
  page.get_by_role('button',name='新建问答').click();expect(img).to_have_count(0)
  page.get_by_label('知识库会话').select_option(image_sid)
  expect(page.get_by_role('heading',name='排版测试')).to_be_visible();expect(img).to_be_visible()
  checks.append('cloud assistant text omitted: structured text block restores Markdown and images')

  # A long answer must not start every remote image request at once.
  page.evaluate(r"""()=>{window.__pendingImages=[];window.biodsh.cloudQa=(a,b)=>a==='chat'?Promise.resolve({answer:Array.from({length:8},(_,i)=>`![queue${i}](/api/figures/proxy?n=${i})`).join('\n\n'),sources:[]}):a==='image'?new Promise(resolve=>window.__pendingImages.push(resolve)):window.__cloudOriginal(a,b)}""")
  q.fill('图片并发测试');page.get_by_role('button',name='提问',exact=True).click()
  page.wait_for_function('window.__pendingImages.length===3');page.wait_for_timeout(200)
  assert page.evaluate('window.__pendingImages.length')==3
  page.evaluate("window.__pendingImages[0]({src:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jf1sAAAAASUVORK5CYII='})")
  page.wait_for_function('window.__pendingImages.length===4')
  page.get_by_role('button',name='新建问答').click()
  page.evaluate("window.__pendingImages.slice(1).forEach(resolve=>resolve({src:'bad'}))")
  page.wait_for_timeout(300);assert page.evaluate('window.__pendingImages.length')==4
  checks.append('image queue limits concurrent requests to three and cancels obsolete queued images')

  for width in [760,1280]:
   page.set_viewport_size({'width':width,'height':900});expect(page.get_by_label('科研问题',exact=True)).to_be_visible()
   assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
  page.screenshot(path=str(out/'knowledge.png'),full_page=True)
  page.get_by_role('button',name='设置',exact=True).click();page.get_by_role('tab',name='账号与支付').click()
  page.get_by_role('button',name='退出 / 切换账号').click();expect(page.get_by_role('form',name='登录 OMaaX')).to_be_visible();expect(page.get_by_text('研究证据摘要')).to_have_count(0)
  checks.append('logout removes account knowledge content')
  assert not errors,errors
  assert not external,external
 finally:
  (out/'result.json').write_text(json.dumps({'checks':checks,'errors':errors,'externalRequests':external},ensure_ascii=False,indent=2),encoding='utf-8');browser.close()
print(json.dumps({'passed':len(checks),'errors':errors},ensure_ascii=False))
