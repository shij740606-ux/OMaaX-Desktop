"""Real knowledge UI with deterministic stream events; no model or user account calls."""
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect
BASE = os.environ.get('OMAAX_QA_URL', 'http://127.0.0.1:41740').rstrip('/')

out = ROOT / 'validation-local/cloud-stream-browser'
out.mkdir(parents=True, exist_ok=True)
checks, errors, external = [], [], []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
    page = browser.new_page(viewport={'width':1280, 'height':900})
    page.on('pageerror', lambda e: errors.append(str(e)))
    def route(request):
        if request.request.url.startswith((BASE+'/', BASE.replace('http:', 'ws:')+'/', 'data:', 'blob:')):
            request.continue_()
        else:
            external.append(request.request.url)
            request.abort()
    page.route('**/*', route)
    try:
        page.goto(BASE+'/workflow-preview.html?auth=qa&cloud=1')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='知识库问答', exact=True).click()
        page.evaluate("""() => {
          const original=window.biodsh.cloudQa;
          window.__qaStreams=[];window.__qaSaved=[];window.__qaCancelled=[];window.__qaCopied='';
          Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async t=>window.__qaCopied=t}});
          window.biodsh.cloudQa=(action,body={},onEvent)=>{
            if(action==='chat')return new Promise((resolve,reject)=>window.__qaStreams.push({body,onEvent,resolve,reject}));
            if(action==='cancel')window.__qaCancelled.push(body.requestId);
            if(action==='message'&&body.role==='assistant')window.__qaSaved.push(body);
            return original(action,body);
          };
        }""")
        question = page.get_by_label('科研问题', exact=True)
        def ask(text, index):
            question.fill(text)
            page.get_by_role('button', name='提问', exact=True).click()
            page.wait_for_function('(n)=>window.__qaStreams.length===n', arg=index+1)
        def emit(index, event, payload):
            page.evaluate('([i,event,payload])=>window.__qaStreams[i].onEvent({event,payload})', [index,event,payload])
        ask('SPL 的研究依据', 0)
        expect(page.locator('.knowledge-process').last).to_be_visible()
        expect(page.get_by_role('button',name='中止',exact=True)).to_be_visible()
        emit(0,'progress',{'stage':'entities_identified','phase':'planning','label':'已识别实体','details':{'entities':['SPL','rice'],'private_reasoning':'SHOULD_NOT_DISPLAY'}})
        emit(0,'progress',{'stage':'evidence_accepted','phase':'evidence','label':'证据已采纳','details':{'accepted_count':12}})
        expect(page.locator('.knowledge-process').last).to_contain_text('证据已采纳')
        expect(page.locator('.knowledge-process').last).to_contain_text('SPL')
        assert 'SHOULD_NOT_DISPLAY' not in page.locator('body').inner_text()
        emit(0,'answer_delta',{'content':'这是生成中的草稿。'})
        emit(0,'answer_delta',{'content':'后续内容已到达。'})
        expect(page.locator('.knowledge-draft')).to_contain_text('这是生成中的草稿。后续内容已到达。')
        page.screenshot(path=str(out/'progress-zh.png'))
        checks.append('Actual stages and public details appear before completion; draft accumulates; private fields omitted')
        page.evaluate('(result)=>window.__qaStreams[0].resolve(result)', {'answer':'## 最终回答\n\n已核对的完整答案。','sources':[{'title':'SPL paper','doi':'10.1234/fixture','snippet':'Evidence excerpt'}]})
        expect(page.get_by_role('heading',name='最终回答',exact=True)).to_be_visible()
        expect(page.locator('.knowledge-draft')).to_have_count(0)
        assert '这是生成中的草稿。' not in page.locator('body').inner_text()
        page.wait_for_function('window.__qaSaved.length===1')
        saved=page.evaluate('window.__qaSaved[0]')
        assert saved['text'].startswith('## 最终回答') and 'process' not in saved
        assert [b['type'] for b in saved['blocks']]==['sources']
        process=page.locator('.knowledge-process').last
        assert process.get_attribute('open') is None
        process.locator('summary').click()
        page.get_by_role('button',name='复制过程',exact=True).click()
        assert 'SPL' in page.evaluate('window.__qaCopied')
        page.get_by_role('button',name='复制回答和来源',exact=True).click()
        assert 'Evidence excerpt' in page.evaluate('window.__qaCopied')
        page.screenshot(path=str(out/'complete-zh.png'))
        checks.append('Final replaces draft; progress expands and copies; only final answer and sources persist')
        ask('取消中的问题',1)
        emit(1,'answer_delta',{'content':'中止前的部分内容'})
        expect(page.locator('.knowledge-draft')).to_contain_text('中止前的部分内容')
        page.get_by_role('button',name='中止',exact=True).click()
        expect(question).to_be_enabled()
        expect(page.locator('.knowledge-draft')).to_contain_text('中止前的部分内容')
        assert len(page.evaluate('window.__qaCancelled'))==1
        ask('下一条问题',2)
        emit(1,'answer_delta',{'content':'LATE_OLD_CONTENT'})
        page.evaluate("()=>window.__qaStreams[1].resolve({answer:'LATE_OLD_ANSWER',sources:[]})")
        emit(2,'answer_delta',{'content':'新问题的部分答案'})
        expect(page.locator('.knowledge-draft').last).to_contain_text('新问题的部分答案')
        page.evaluate("()=>window.__qaStreams[2].reject(Error('知识库连接中断，请重试'))")
        expect(question).to_be_enabled()
        assert 'LATE_OLD' not in page.locator('body').inner_text()
        assert len(page.evaluate('window.__qaSaved'))==1
        expect(page.locator('.knowledge-draft').last).to_contain_text('新问题的部分答案')
        checks.append('Cancel sends original request ID; late events ignored; failures retain partial text without saving')
        page.evaluate("async()=>{const m=await import('/src/i18n.ts');m.syncLanguage('en');}")
        expect(page.get_by_role('heading',name='Literature Knowledge Q&A',exact=True)).to_be_visible()
        # English process labels must be local UI text even when service labels are Chinese.
        question=page.locator('#knowledge-question')
        question.fill('English question')
        page.get_by_role('button',name='Ask',exact=True).click()
        page.wait_for_function('window.__qaStreams.length===4')
        emit(3,'progress',{'stage':'evidence_review_completed','phase':'evidence','label':'证据审核完成','details':{'accepted_count':8}})
        expect(page.locator('.knowledge-process').last).to_contain_text('Evidence review completed')
        emit(3,'progress',{'stage':'future_stage','phase':'retrieval','label':'未来新增阶段','details':{}})
        expect(page.locator('.knowledge-process').last).to_contain_text('Searching the literature')
        assert not any('\u4e00'<=c<='\u9fff' for c in page.locator('.knowledge-process').last.inner_text())
        page.set_viewport_size({'width':760,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.screenshot(path=str(out/'progress-en-narrow.png'))
        page.get_by_role('button',name='Stop',exact=True).click()
        checks.append('English known and unknown phases localize; narrow layout fits; stop remains available')
        assert not errors, errors
        assert not external, external
    finally:
        (out/'result.json').write_text(json.dumps({'checks':checks,'errors':errors,'externalRequests':external},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
print(json.dumps({'passed':len(checks),'errors':errors},ensure_ascii=False))
