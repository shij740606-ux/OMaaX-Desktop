"""Browser contract tests for retained Q&A images and independent research selectors.

The IPC fixture is deliberately local and synthetic. Native on-disk cache behavior
is tested separately; this test checks the real renderer's requests and UI state.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('OMAAX_QA_URL', 'http://127.0.0.1:41757').rstrip('/')
OUT = Path(os.environ.get('OMAAX_QA_OUT', str(ROOT / 'provenance/pending/knowledge-retention-057/browser')))
OUT.mkdir(parents=True, exist_ok=True)
A = '10000000-0000-4000-8000-000000000001'
B = '10000000-0000-4000-8000-000000000002'
C = '10000000-0000-4000-8000-000000000003'
P1, P2 = 'C:/fixture/research-one', 'C:/fixture/research-two'

FIXTURE = r"""() => {
  const key='omaax-knowledge-retention-fixture';
  const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
  const ids=['10000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000003'];
  const urls=['/api/figures/proxy?url=https%3A%2F%2Fpaper.example%2Fa.png&sig=original',
    '/api/figures/proxy?url=https%3A%2F%2Fpaper.example%2Fb.png&sig=original',
    '/api/figures/proxy?url=https%3A%2F%2Fpaper.example%2Fc.png&sig=expired'];
  const initial={sessions:ids.map((id,i)=>({id,title:['YABBY study','Mouse study','Expired image study'][i],messages:[
    {role:'user',text:['What does YABBY do?','How does the mouse pathway work?','Which figure is available?'][i]},
    {role:'assistant',text:['YABBY evidence.','Mouse evidence.','Unretained figure.'][i]+'\n\n![Figure '+(i+1)+']('+urls[i]+')',blocks:[{type:'sources',items:[{id:'ref-'+i,title:['YABBY paper','Mouse paper','Figure paper'][i],doi:'10.1000/test-'+i}]}]}
  ]})),records:{},cache:{},expired:false};
  const r=window.__retention={...(JSON.parse(localStorage.getItem(key)||'null')||initial),calls:[],deferred:[],delayDetail:false,failMessage:false,failRetention:ids[1],denyUncached:true};
  r.persist=()=>localStorage.setItem(key,JSON.stringify({sessions:r.sessions,records:r.records,cache:r.cache,expired:r.expired}));
  const old=window.biodsh.dshRpc;
  window.biodsh.dshRpc=async(method,body)=>method==='workspace.list'?{items:[
    {workspaceId:'p1',path:'C:/fixture/research-one',title:'Research one',sessionIds:[]},
    {workspaceId:'p2',path:'C:/fixture/research-two',title:'Research two',sessionIds:[]}
  ],archivedSessionIds:[]}:old(method,body);
  window.biodsh.cloudQa=async(action,body={},onEvent)=>{
    r.calls.push({action,body:structuredClone(body)});
    if(action==='list')return structuredClone(r.sessions);
    if(action==='detail'){
      if(r.delayDetail)await new Promise(resolve=>r.deferred.push(resolve));
      const session=r.sessions.find(s=>s.id===body.id);if(!session)throw Error('Session missing');return structuredClone(session);
    }
    if(action==='image'){
      if(!body.sessionId)throw Error('Renderer omitted the Q&A session ID');
      const cacheKey=body.sessionId+'|'+body.url;
      if(r.cache[cacheKey])return {src:r.cache[cacheKey],persisted:true,fromCache:true};
      if(r.expired||(r.denyUncached&&body.url===urls[2]))throw Error('IMAGE_DENIED: expired signature');
      if(r.failRetention===body.sessionId)return {src:png,persisted:false};
      r.cache[cacheKey]=png;r.persist();return {src:png,persisted:true};
    }
    if(action==='save_image')return {saved:true};
    if(action==='begin'||action==='cancel')return {ok:true};
    if(action==='create'){const session={id:crypto.randomUUID(),title:body.title,messages:[]};r.sessions.push(session);r.persist();return session;}
    if(action==='message'){
      if(body.role==='assistant'&&r.failMessage)throw Error('Synthetic save failure');
      r.sessions.find(s=>s.id===body.id).messages.push({...body});r.persist();return body;
    }
    if(action==='chat')return {answer:'New answer with a figure.\n\n![New figure]('+urls[0]+')',sources:[]};
    if(action==='delete'){r.sessions=r.sessions.filter(s=>s.id!==body.id);r.persist();return {ok:true};}
    throw Error('Unexpected fixture action '+action);
  };
  window.biodsh.research=async(action,body={})=>{
    r.calls.push({action:'research:'+action,body:structuredClone(body)});
    if(action==='load')return structuredClone(r.records[body.project]||null);
    if(action==='save'){
      const previous=r.records[body.project];
      if((previous?.revision||0)!==body.expectedRevision)throw Error('RESEARCH_CONFLICT');
      r.records[body.project]={...structuredClone(body.state),revision:body.expectedRevision+1,updatedAt:new Date().toISOString()};
      r.persist();return structuredClone(r.records[body.project]);
    }
    if(action==='cancel')return {ok:true};
    throw Error('No model generation should occur in this test');
  };
}"""


def install_fixture(page):
    page.evaluate(FIXTURE)


def research(page):
    page.get_by_role('button', name='课题研究', exact=True).click()


def qa(page):
    page.get_by_role('button', name='文献问答', exact=True).click()


def expand_materials(page):
    details = page.locator('.research-materials')
    if not details.evaluate('(el)=>el.open'):
        details.locator(':scope > summary').click()


checks, errors, external = [], [], []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
    page = browser.new_page(viewport={'width':1440, 'height':1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith((BASE+'/', BASE.replace('http:', 'ws:')+'/', 'data:', 'blob:')) else (external.append(route.request.url), route.abort()))
    try:
        page.goto(BASE+'/workflow-preview.html?auth=qa&cloud=1')
        page.wait_for_load_state('networkidle')
        install_fixture(page)
        page.get_by_role('button', name='知识库问答', exact=True).click()
        research(page)
        source = page.get_by_label('选择问答来源', exact=True)
        destination = page.get_by_label('保存到哪个项目', exact=True)
        page.get_by_role('button', name='刷新项目', exact=True).click()
        page.get_by_role('button', name='刷新问答来源', exact=True).click()
        expect(destination).to_have_value(P1)
        expect(source.locator('option')).to_contain_text(['选择问答', 'YABBY study', 'Mouse study', 'Expired image study'])
        source.select_option(A)
        expect(source).to_have_value(A)
        expect(source).to_be_enabled()
        expect(destination).to_have_value(P1)
        expect(page.locator('.research-meta')).to_contain_text('已收录 0 条资料')
        assert not page.locator('.research-materials').evaluate('(el)=>el.open')
        assert not page.evaluate("window.__retention.calls.some(c=>c.action==='research:generate')")
        checks.append('Research exposes separate source and destination selectors; source choice alone neither imports nor calls a model')

        qa(page)
        expect(page.get_by_label('知识库会话', exact=True)).to_have_value(A)
        expect(page.locator('.knowledge-message-answer img')).to_have_count(1)
        page.wait_for_function("document.querySelector('.knowledge-message-answer img').naturalWidth>0")
        assert page.evaluate("window.__retention.calls.filter(c=>c.action==='image').every(c=>c.body.sessionId===window.__retention.sessions[0].id)")
        page.screenshot(path=str(OUT/'retained-answer-zh.png'), animations='disabled')
        checks.append('Historical Q&A images render through the native image IPC contract with the correct source session ID')

        research(page)
        expand_materials(page)
        page.get_by_role('button', name='收录当前问答结果', exact=True).click()
        page.get_by_label('课题名称', exact=True).fill('What does YABBY do?')
        expect(page.get_by_label('课题名称', exact=True)).to_have_value('What does YABBY do?')
        expect(page.locator('.research-meta')).to_contain_text('已收录 3 条资料')
        page.get_by_role('button', name='保存研究内容', exact=True).click()
        expect(page.locator('.research-notice')).to_contain_text('研究内容已保存到当前项目')
        assert page.evaluate("window.__retention.records['C:/fixture/research-one'].evidence.every(e=>!JSON.stringify(e).includes('Mouse'))")
        page.evaluate('window.__retention.delayDetail=true')
        source.select_option(B)
        expect(source).to_be_disabled()
        expect(page.get_by_role('button', name='收录当前问答结果', exact=True)).to_be_disabled()
        expect(destination).to_have_value(P1)
        page.evaluate('window.__retention.delayDetail=false;window.__retention.deferred.splice(0).forEach(resolve=>resolve())')
        expect(source).to_have_value(B)
        expect(source).to_be_enabled()
        expect(page.get_by_label('课题名称', exact=True)).to_have_value('What does YABBY do?')
        expect(page.locator('.research-meta')).to_contain_text('已收录 3 条资料')
        checks.append('Explicit collection uses the selected Q&A only; loading another source disables collection and does not replace the saved project')

        destination.select_option(P2)
        expect(page.get_by_label('课题名称', exact=True)).to_have_value('')
        expect(source).to_have_value(B)
        expand_materials(page)
        page.get_by_role('button', name='收录当前问答结果', exact=True).click()
        page.get_by_label('课题名称', exact=True).fill('How does the mouse pathway work?')
        expect(page.get_by_label('课题名称', exact=True)).to_have_value('How does the mouse pathway work?')
        page.once('dialog', lambda dialog: dialog.dismiss())
        destination.select_option(P1)
        expect(destination).to_have_value(P2)
        page.get_by_role('button', name='保存研究内容', exact=True).click()
        expect(page.locator('.research-notice')).to_contain_text('研究内容已保存到当前项目')
        destination.select_option(P1)
        expect(page.get_by_label('课题名称', exact=True)).to_have_value('What does YABBY do?')
        expect(source).to_have_value(B)
        assert page.evaluate("Object.keys(window.__retention.records).length") == 2
        assert page.evaluate("window.__retention.records['C:/fixture/research-two'].evidence.every(e=>!JSON.stringify(e).includes('YABBY'))")
        page.screenshot(path=str(OUT/'separate-source-and-destination-zh.png'), animations='disabled')
        checks.append('Changing save destination preserves Q&A source; dirty switching can be cancelled; projects save and restore independent material')

        qa(page)
        expect(page.locator('.knowledge-message-answer img')).to_have_count(1)
        expect(page.get_by_role('button', name='重试保留图片', exact=True)).to_be_visible()
        expect(page.get_by_text('图片已显示，但未能随会话保存到本机。请重试保留或另存图片。', exact=False)).to_be_visible()
        page.evaluate('window.__retention.failRetention=""')
        page.get_by_role('button', name='重试保留图片', exact=True).click()
        expect(page.get_by_role('button', name='重试保留图片', exact=True)).to_have_count(0)
        expect(page.locator('.knowledge-message-answer img')).to_have_count(1)
        checks.append('Cache write failure keeps the displayed image and exposes a retry; successful retry removes the warning')

        research(page)
        source.select_option(C)
        expect(source).to_have_value(C)
        expect(source).to_be_enabled()
        qa(page)
        expect(page.locator('.knowledge-message-answer img')).to_have_count(0)
        expect(page.get_by_role('button', name='重新加载图片', exact=True)).to_be_visible()
        expect(page.get_by_text('此图片尚未保存在本机，原链接已失效或无权访问。请重新查询以获取图片。', exact=False)).to_be_visible()
        page.evaluate('window.__retention.denyUncached=false')
        page.get_by_role('button', name='重新加载图片', exact=True).click()
        expect(page.locator('.knowledge-message-answer img')).to_have_count(1)
        checks.append('Previously unretained expired images show a truthful recovery message and can retry without affecting answer text')

        page.evaluate('window.__retention.expired=true;window.__retention.persist()')
        page.reload()
        page.wait_for_load_state('networkidle')
        install_fixture(page)
        page.get_by_role('button', name='知识库问答', exact=True).click()
        research(page)
        page.get_by_role('button', name='刷新项目', exact=True).click()
        page.get_by_role('button', name='刷新问答来源', exact=True).click()
        source.select_option(A)
        expect(source).to_be_enabled()
        expect(page.get_by_label('课题名称', exact=True)).to_have_value('What does YABBY do?')
        qa(page)
        expect(page.locator('.knowledge-message-answer img')).to_have_count(1)
        page.wait_for_function("document.querySelector('.knowledge-message-answer img').naturalWidth>0")
        assert page.evaluate('window.__retention.expired')
        checks.append('Renderer reload restores saved research and can display a retained image even when the original image link is now unavailable (synthetic IPC cache)')

        page.evaluate('window.__retention.failMessage=true;window.__retention.expired=false')
        page.get_by_label('科研问题', exact=True).fill('Keep this answer even if save fails')
        page.get_by_role('button', name='提问', exact=True).click()
        expect(page.get_by_role('button', name='重试保存', exact=True)).to_be_visible()
        research(page)
        expect(source).to_be_disabled()
        page.get_by_role('button', name='文献问答', exact=True).click()
        page.evaluate('window.__retention.failMessage=false')
        page.get_by_role('button', name='重试保存', exact=True).click()
        expect(page.get_by_role('button', name='重试保存', exact=True)).to_have_count(0)
        research(page)
        expect(source).to_be_enabled()
        checks.append('An unsaved answer blocks source switching until retry succeeds, preserving answer text and current session')

        page.evaluate("async()=>{const m=await import('/src/i18n.ts');m.syncLanguage('en')}")
        expect(page.get_by_label('Select Q&A source', exact=True)).to_be_visible()
        expect(page.get_by_label('Save to project', exact=True)).to_be_visible()
        for width, height in [(1440,1000),(768,850)]:
            page.set_viewport_size({'width':width,'height':height})
            expect(page.get_by_label('Select Q&A source', exact=True)).to_be_in_viewport()
            expect(page.get_by_label('Save to project', exact=True)).to_be_in_viewport()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(OUT/f'selectors-en-{width}.png'), animations='disabled')
        checks.append('Source/destination controls translate to English and stay accessible at desktop and narrow widths')
        assert not errors, errors
        assert not external, external
    except Exception:
        page.screenshot(path=str(OUT/'failure.png'), animations='disabled')
        (OUT/'failure.html').write_text(page.content(), encoding='utf-8')
        raise
    finally:
        (OUT/'result.json').write_text(json.dumps({'checks':checks,'pageErrors':errors,'externalRequests':external,'boundary':'Real renderer with isolated synthetic IPC; native disk cache is validated separately.'}, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
print(json.dumps({'passed':len(checks),'errors':errors}, ensure_ascii=False))
