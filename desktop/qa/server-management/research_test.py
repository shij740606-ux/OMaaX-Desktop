"""Actual research UI against an isolated in-memory model/IPC fixture."""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('OMAAX_QA_URL', 'http://127.0.0.1:41747').rstrip('/')
OUT = Path(os.environ.get('OMAAX_QA_OUT', str(ROOT / 'provenance/releases/0.4.55/research-browser')))
OUT.mkdir(parents=True, exist_ok=True)
checks, errors, external = [], [], []
theme_colors = []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
    page = browser.new_page(viewport={'width':1440, 'height':1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith((BASE+'/', BASE.replace('http:', 'ws:')+'/', 'data:', 'blob:')) else (external.append(route.request.url), route.abort()))
    try:
        page.goto(BASE+'/workflow-preview.html?auth=qa&cloud=1')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button', name='知识库问答', exact=True).click()
        expect(page.get_by_role('button', name='课题研究', exact=True)).to_be_visible()
        page.evaluate("""() => {
          const old=window.biodsh.dshRpc;
          window.__research={records:{},calls:[],pending:[],defer:false,prompts:[],newSessions:[]};
          const r=window.__research;
          window.biodsh.dshRpc=async(method,body)=>{
            if(method==='workspace.list')return {items:[
              {workspaceId:'p1',path:'C:/fixture/research-one',title:'Research one',sessionIds:[]},
              {workspaceId:'p2',path:'C:/fixture/research-two',title:'Research two',sessionIds:[]}
            ],archivedSessionIds:[]};
            if(method==='research.handoff'){r.prompts.push(body);return {accepted:true};}
            return old(method,body);
          };
          window.biodsh.dshNewSession=async id=>{r.newSessions.push(id);return 'fixture-research-session';};
          window.biodsh.dshSetReasoning=async()=>{};
          window.biodsh.workspaceFiles=async()=>[];
          window.biodsh.research=async(action,body={})=>{
            r.calls.push({action,body:structuredClone(body)});
            if(action==='load')return structuredClone(r.records[body.project]||null);
            if(action==='save'){
              if((r.records[body.project]?.revision||0)!==body.expectedRevision)throw Error('RESEARCH_CONFLICT');
              r.records[body.project]={...structuredClone(body.state),revision:body.expectedRevision+1,updatedAt:new Date().toISOString()};
              return structuredClone(r.records[body.project]);
            }
            if(action==='cancel')return {ok:true};
            if(action==='generate'){
              const ids=body.evidence.slice(0,1).map(e=>e.id);
              const en=body.lang==='en';
              const analysis={title:en?'Rice research plan':'水稻研究规划',summary:en?'Separate source observations from hypotheses.':'区分已有资料与待验证的研究假设。',nodes:[
                {id:'n1',kind:'question',label:en?'Salt tolerance question':'耐盐研究问题',summary:en?'Question collected from the project.':'当前项目收录的研究问题。',basis:'source',evidenceIds:ids},
                {id:'n2',kind:'method',label:en?'Compare expression':'比较表达模式',summary:en?'Requires replicated measurements.':'需要有生物学重复的实际测量数据。',basis:'hypothesis',evidenceIds:ids}],
              edges:[{id:'e1',source:'n1',target:'n2',label:en?'Possible next step':'可进一步研究',basis:'hypothesis',evidenceIds:ids}],
              suggestions:[{id:'s1',title:en?'Check expression evidence':'核对表达证据',reason:en?'Test the proposed relationship.':'验证候选关系。',requirements:[en?'Existing expression matrix':'已有表达矩阵'],steps:[en?'Inspect existing project results':'核对项目已有结果',en?'Use the built-in analysis workflow':'使用内置分析流程'],evidenceIds:ids,limitations:en?'A proposed plan; not a completed finding.':'这是待验证的规划，尚未完成实验。'}]};
              if(r.defer)return new Promise(resolve=>r.pending.push({resolve,analysis}));
              return analysis;
            }
          };
        }""")
        page.get_by_role('button',name='课题研究',exact=True).click()
        page.get_by_role('button', name='刷新项目', exact=True).click()
        expect(page.get_by_label('保存到哪个项目',exact=True)).to_have_value('C:/fixture/research-one')
        page.get_by_role('button',name='文献问答',exact=True).click()
        page.get_by_label('科研问题',exact=True).fill('How should rice salt tolerance be studied?')
        page.get_by_role('button',name='提问',exact=True).click()
        expect(page.locator('.knowledge-message-answer')).to_have_count(1)
        expect(page.get_by_label('科研问题',exact=True)).to_be_enabled()
        page.get_by_role('button',name='课题研究',exact=True).click()
        expect(page.get_by_label('课题名称',exact=True)).to_be_enabled()
        assert not page.evaluate("window.__research.calls.some(c=>c.action==='generate')")
        expect(page.locator('.knowledge-hub-bar nav button')).to_have_count(2)
        assert not page.locator('.research-materials').evaluate('(el)=>el.open')
        expect(page.get_by_role('tabpanel')).to_contain_text('让研究线索连成图谱')
        page.screenshot(path=str(OUT/'empty-map-zh.png'))
        page.get_by_role('tab',name='下一步建议',exact=True).click()
        expect(page.get_by_role('tabpanel')).to_contain_text('把研究问题变成下一步行动')
        page.get_by_role('tab',name='下一步建议',exact=True).press('Home')
        expect(page.get_by_role('tab',name='关系图谱',exact=True)).to_be_focused()
        expect(page.get_by_role('tab',name='关系图谱',exact=True)).to_have_attribute('aria-selected','true')
        assert not page.evaluate("window.__research.calls.some(c=>c.action==='generate')")
        checks.append('One project research entry; empty sources default collapsed; distinct empty views and keyboard tabs do not call the model')
        page.get_by_label('课题名称',exact=True).fill('Rice salt tolerance')
        page.locator('.research-materials > summary').click()
        page.get_by_role('button',name='收录当前问答结果',exact=True).click()
        page.locator('.research-materials > summary').click()
        expect(page.locator('.research-meta')).to_contain_text('已收录 4 条资料')
        page.get_by_role('button',name='保存研究内容',exact=True).click()
        expect(page.locator('.research-notice')).to_contain_text('研究内容已保存到当前项目')
        page.get_by_role('button',name='生成图谱与建议',exact=True).click()
        expect(page.locator('.research-graph .react-flow__node')).to_have_count(2)
        page.get_by_label('查看节点',exact=True).select_option('n1')
        expect(page.get_by_label('图谱详情',exact=True)).to_contain_text('How should rice salt tolerance be studied?')
        page.locator('.research-planner').evaluate('(el)=>{el.scrollTop=el.scrollHeight;}')
        assert page.locator('.research-planner').evaluate('(el)=>el.clientHeight<el.scrollHeight && el.scrollTop>0')
        page.screenshot(path=str(OUT/'map-zh.png'))
        checks.append('Original Q&A completes; explicit collection preserves references; model map renders and scrolls')

        page.get_by_role('tab',name='下一步建议',exact=True).click()
        expect(page.locator('.research-suggestion')).to_contain_text('待核实与限制')
        page.screenshot(path=str(OUT/'suggestions-zh.png'))
        page.get_by_label('保存到哪个项目',exact=True).select_option('C:/fixture/research-two')
        expect(page.get_by_label('课题名称',exact=True)).to_have_value('')
        page.get_by_label('课题名称',exact=True).fill('Other project draft')
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.get_by_label('保存到哪个项目',exact=True).select_option('C:/fixture/research-one')
        expect(page.get_by_label('保存到哪个项目',exact=True)).to_have_value('C:/fixture/research-two')
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_label('保存到哪个项目',exact=True).select_option('C:/fixture/research-one')
        expect(page.get_by_label('课题名称',exact=True)).to_have_value('Rice salt tolerance')
        expect(page.locator('.research-suggestion')).to_have_count(1)
        assert not page.locator('.research-materials').evaluate('(el)=>el.open')
        checks.append('Project content reloads independently; unsaved project switch can be cancelled or discarded')

        page.evaluate('window.__research.defer=true')
        page.get_by_role('button',name='更新图谱与建议',exact=True).click()
        page.wait_for_function('window.__research.pending.length===1')
        page.get_by_role('button',name='取消更新',exact=True).click()
        expect(page.locator('.research-notice')).to_contain_text('已取消更新')
        count=page.evaluate("window.__research.calls.filter(c=>c.action==='save').length")
        page.evaluate("()=>{window.__research.pending[0].resolve({...window.__research.pending[0].analysis,title:'LATE_RESULT_MUST_NOT_APPEAR'});window.__research.defer=false;}")
        page.get_by_role('tab',name='关系图谱',exact=True).click()
        expect(page.locator('.research-analysis-summary')).not_to_contain_text('LATE_RESULT_MUST_NOT_APPEAR')
        assert count==page.evaluate("window.__research.calls.filter(c=>c.action==='save').length")
        assert page.evaluate("window.__research.calls.some(c=>c.action==='cancel')")
        checks.append('Cancel sends the request ID, keeps the saved map, and ignores late model completion')

        page.evaluate("async()=>{const m=await import('/src/i18n.ts');m.syncLanguage('en');}")
        page.get_by_role('button',name='Update map and suggestions',exact=True).click()
        expect(page.locator('.research-analysis-summary')).to_contain_text('Rice research plan')
        for selector in ['.research-toolbar','.research-graph-heading','.research-meta','.knowledge-hub-bar','.research-view-tabs','.research-materials > summary']:
            assert not any('\u4e00'<=c<='\u9fff' for c in page.locator(selector).inner_text()), selector
        expect(page.get_by_role('button',name='Fit map to view',exact=True)).to_be_visible()
        page.set_viewport_size({'width':768,'height':850})
        page.locator('.research-planner').evaluate('(el)=>{el.scrollTop=el.scrollHeight;}')
        page.wait_for_function("document.querySelector('.research-graph .react-flow__node').getBoundingClientRect().width>=200")
        page.get_by_role('button',name='Fit map to view',exact=True).click()
        page.wait_for_function("""() => {
          const graph=document.querySelector('.research-graph').getBoundingClientRect();
          return [...document.querySelectorAll('.research-graph .react-flow__node')].every(node=>{
            const box=node.getBoundingClientRect();return box.left>=graph.left && box.right<=graph.right;
          });
        }""")
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.screenshot(path=str(OUT/'map-en-narrow.png'))
        page.get_by_role('tab',name='Next steps',exact=True).click()
        expect(page.locator('.research-suggestion')).to_contain_text('Limitations and items to verify')
        page.evaluate("document.documentElement.dataset.theme='dark'")
        page.wait_for_function(r"""() => [...document.querySelectorAll('.research-view-tab')].every(el=>{
          const rgb=getComputedStyle(el).backgroundColor.match(/[\d.]+/g).map(Number);
          return rgb.slice(0,3).every(channel=>channel<100);
        })""")
        page.locator('.research-planner').evaluate('(el)=>{el.scrollTop=0;}')
        assert page.locator('.research-planner').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
        page.screenshot(path=str(OUT/'suggestions-en-dark.png'), animations='disabled')
        theme_colors = page.locator('.research-view-tab').evaluate_all("els=>els.map(el=>({label:el.getAttribute('aria-label'),background:getComputedStyle(el).backgroundColor,description:getComputedStyle(el.querySelector('small')).color}))")
        checks.append('English map, suggestions and controls localize; narrow layout scrolls without horizontal overflow')

        page.get_by_role('button',name='Continue in chat',exact=True).click()
        page.wait_for_function('window.__research.prompts.length===1')
        handoff=page.evaluate('window.__research.prompts[0]')
        assert handoff['sessionId']=='fixture-research-session'
        assert page.evaluate('window.__research.newSessions')==['p1']
        assert len(handoff['requestId'])==36 and handoff['project']=='C:/fixture/research-one'
        assert 'Check expression evidence' in handoff['summary']
        assert 'Rice salt tolerance' not in handoff['summary']
        assert len(handoff['summary'])<250
        assert 'Existing expression matrix' in handoff['context']
        assert 'Existing expression matrix' not in handoff['summary']
        page.get_by_role('button',name='Knowledge Q&A',exact=True).click()
        expect(page.locator('.research-suggestion')).to_contain_text('Sent to chat')
        page.get_by_role('button',name='Literature Q&A',exact=True).click()
        expect(page.locator('.knowledge-message-answer')).to_have_count(1)
        expect(page.get_by_role('button',name='Copy answer and sources',exact=True)).to_be_visible()
        checks.append('Readable task is accepted by a session in the selected project without internal rules/JSON; original Q&A remains available')
        assert not errors, errors
        assert not external, external
    except Exception:
        page.screenshot(path=str(OUT/'failure.png'))
        (OUT/'failure.html').write_text(page.content(),encoding='utf-8')
        raise
    finally:
        (OUT/'result.json').write_text(json.dumps({'checks':checks,'errors':errors,'externalRequests':external,'darkThemeColors':theme_colors},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
print(json.dumps({'passed':len(checks),'errors':errors},ensure_ascii=False))
