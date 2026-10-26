"""Real React document delivery UI, with isolated projects and a mocked native bridge."""
import json, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect
BASE = os.environ.get('OMAAX_QA_URL', 'http://127.0.0.1:41740').rstrip('/')

out=ROOT/'validation-local/documents'
out.mkdir(parents=True,exist_ok=True)
checks,errors,external=[],[],[]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    page=browser.new_page(viewport={'width':1280,'height':900})
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(request):
        if request.request.url.startswith((BASE+'/',BASE.replace('http:', 'ws:')+'/', 'data:','blob:')):
            request.continue_()
        else:
            external.append(request.request.url)
            request.abort()
    page.route('**/*',route)
    try:
        page.goto(BASE+'/workflow-preview.html?auth=qa&cloud=1')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button',name='分析结果',exact=True).wait_for()
        page.evaluate("""()=>{
          const api=window.biodsh,original=api.dshRpc;
          const projects=[{workspaceId:'doc-a',path:'C:/isolated-ui-preview/Project A',title:'Project A',sessionIds:['doc-session-a'],updatedAt:''},{workspaceId:'doc-b',path:'C:/isolated-ui-preview/Project B',title:'Project B',sessionIds:['doc-session-b'],updatedAt:''},{workspaceId:'empty',path:'C:/isolated-ui-preview/Empty',title:'Empty project',sessionIds:[],updatedAt:''}];
          const sessions=projects.flatMap(w=>w.sessionIds.map(sessionId=>({sessionId,updatedAt:1,running:false,blank:false,projections:{values:{title:sessionId==='doc-session-a'?'Report conversation':'Other conversation'}}})));
          window.__documents={opened:[],prompts:[],created:[],missing:false,projects};
          api.dshRpc=async(method,payload)=>{
            if(method==='workspace.list')return {items:projects,archivedSessionIds:[]};
            if(method==='session.list')return {items:sessions};
            if(method==='session.prompt'){window.__documents.prompts.push(payload);return {};}
            if(method==='session.reasoning')return {levels:['high'],selected:'high'};
            if(method==='server.files')return [];
            return original(method,payload);
          };
          api.dshNewSession=async workspaceId=>{window.__documents.created.push(workspaceId);const w=projects.find(w=>w.workspaceId===workspaceId);const sessionId='new-document-session';w.sessionIds.push(sessionId);sessions.push({sessionId,updatedAt:2,running:false,blank:false,projections:{values:{title:'Document revision'}}});return sessionId;};
          api.dshSetReasoning=async()=>{};
          api.workspaceFiles=async root=>root.endsWith('/Empty')||!root?[]:[...(root.endsWith('/Project B')?['other-report.docx']:['report-v1.docx','slides-v1.pptx','report-v1.pdf']), 'source.json'].map(name=>({name,rel:'documents/'+name,kind:name.endsWith('.pdf')?'figure':name.endsWith('.json')?'meta':'report',size:2048,modified:1700000000}));
          api.openPath=async path=>{window.__documents.opened.push(path);if(window.__documents.missing)throw Error('missing fixture');};
        }""")
        # Discover real sidebar rows by refreshing its synthetic project listing.
        page.get_by_title('刷新',exact=True).click()
        page.get_by_role('button',name='Report conversation',exact=True).click()
        page.get_by_role('button',name='分析结果',exact=True).click()
        page.get_by_role('button',name='项目文档',exact=True).click()
        docs=page.get_by_role('region',name='项目文档',exact=True)
        expect(docs.locator('article')).to_have_count(3)
        expect(docs).to_contain_text('report-v1.docx')
        expect(docs).not_to_contain_text('source.json')
        expect(page.locator('main select:visible')).to_have_value('C:/isolated-ui-preview/Project A')
        page.screenshot(path=str(out/'project-documents-zh.png'))
        checks.append('Analysis results contains Project documents; current conversation project shows Word, PPT and PDF only')
        docs.get_by_role('button',name='report-v1.docx',exact=True).click()
        page.wait_for_function('window.__documents.opened.length===1')
        assert page.evaluate('window.__documents.opened[0]')=='C:/isolated-ui-preview/Project A\\documents\\report-v1.docx'
        docs.get_by_role('button',name='打开所在文件夹',exact=True).first.click()
        assert page.evaluate('window.__documents.opened[1]')=='C:/isolated-ui-preview/Project A\\documents'
        docs.get_by_role('button',name='继续修改',exact=True).first.click()
        page.wait_for_function('window.__documents.prompts.length===1')
        request=page.evaluate('window.__documents.prompts[0]')
        assert request['sessionId']=='doc-session-a' and '请先询问我希望修改什么' in request['content'][0]['text']
        assert not page.evaluate('window.__documents.created')
        expect(page.get_by_role('button',name='本次分析文件',exact=True)).to_be_visible()
        assert page.get_by_role('button',name='项目文档',exact=True).count()==0
        checks.append('Open and folder actions use exact project paths; same-project revision returns to existing chat and asks for changes')
        page.get_by_role('button',name='分析结果',exact=True).click()
        page.get_by_role('button',name='项目文档',exact=True).click()
        page.locator('main select:visible').select_option('C:/isolated-ui-preview/Empty')
        expect(page.get_by_text('当前项目还没有 Word、PPT 或 PDF 文档。',exact=True)).to_be_visible()
        page.screenshot(path=str(out/'project-documents-empty.png'))
        page.locator('main select:visible').select_option('C:/isolated-ui-preview/Project B')
        expect(page.get_by_role('region',name='项目文档')).to_contain_text('other-report.docx')
        page.get_by_role('button',name='继续修改',exact=True).click()
        page.wait_for_function('window.__documents.prompts.length===2')
        assert page.evaluate('window.__documents.created')==['doc-b']
        request=page.evaluate('window.__documents.prompts[1]')
        assert request['sessionId']=='new-document-session' and '/Project B\\documents\\other-report.docx' in request['content'][0]['text']
        checks.append('Empty project is explained; editing selected project B creates a project B chat instead of sending to project A')
        page.evaluate("async()=>{const m=await import('/src/i18n.ts');m.syncLanguage('en');}")
        page.get_by_role('button',name='Analysis results',exact=True).click()
        page.get_by_role('button',name='Project documents',exact=True).click()
        docs=page.get_by_role('region',name='Project documents',exact=True)
        expect(docs).to_contain_text('other-report.docx')
        expect(page.locator('main select:visible')).to_have_value('C:/isolated-ui-preview/Project B')
        assert not any('\u4e00'<=c<='\u9fff' for c in page.locator('main').inner_text())
        page.set_viewport_size({'width':980,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.screenshot(path=str(out/'project-documents-en.png'))
        page.evaluate('window.__documents.missing=true')
        docs.get_by_role('button',name='Open file',exact=True).click()
        expect(page.get_by_text('Could not open the file. Check that it still exists and a compatible viewer is installed.',exact=True)).to_be_visible()
        checks.append('English labels, project association after navigation, narrow layout and recoverable missing-file error pass')
        assert not errors,errors
        assert not external,external
    finally:
        (out/'ui-result.json').write_text(json.dumps({'checks':checks,'errors':errors,'externalRequests':external},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
print(json.dumps({'passed':len(checks),'errors':errors},ensure_ascii=False))
