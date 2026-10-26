"""Full React shell, synthetic bridge, no model billing or real SSH connections."""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
OUT=ROOT/'validation-local/model-workflow-browser';OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=os.environ.get('OMAAX_QA_BROWSER',r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),headless=True)
 context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();errors=[];external=[];checks=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('request',lambda r:external.append(r.url) if 'should-not-load' in r.url else None)
 try:
  page.goto(os.environ.get('OMAAX_QA_URL','http://127.0.0.1:41740')+'/workflow-preview.html?model=1');page.wait_for_load_state('networkidle')
  (OUT/'initial-dom.html').write_text(page.content(),encoding='utf-8')
  expect(page.get_by_role('button',name='知识库问答',exact=True)).to_be_visible();checks.append('cloud knowledge entry is present alongside analysis')
  page.get_by_role('article').filter(has=page.get_by_role('heading',name='RNA-seq 分析',exact=True)).get_by_role('button').click()
  expect(page.get_by_text('由对话模型配置分析环境',exact=True)).to_be_visible()
  expect(page.get_by_role('button',name='检查并配置所选步骤',exact=True)).to_have_count(0)
  page.get_by_role('button',name='交给模型配置与分析',exact=True).click()
  page.wait_for_function("window.__qa.calls.some(c=>c.action==='session.prompt')")
  calls=page.evaluate('window.__qa.calls');assert not any(c['action']=='workflow_prepare' for c in calls)
  assert any(c['action']=='workflow.select' and c['body']['selection']['steps'] for c in calls);checks.append('model handoff preserves selection, no frontend installation')
  expect(page.get_by_text('推理模式暂不可用',exact=True)).to_be_visible()
  checks.append('malformed mode response does not crash conversation or hide budget controls')
  assert page.evaluate('window.__qa.budget.calls')==81
  expect(page.get_by_role('button',name='确认继续',exact=True)).to_have_count(0)
  assert '达到 80 次' not in page.locator('body').inner_text()
  assert not page.evaluate("window.__qa.calls.some(c=>c.action==='session.budgetReset')")
  checks.append('workflow handoff works with legacy over-limit state; no budget pause or manual renewal')
  page.get_by_role('button',name='分析结果',exact=True).click()
  sources=page.get_by_role('navigation',name='分析结果来源',exact=True)
  expect(sources.get_by_role('button')).to_have_text(['工作区分析','项目文档'])
  expect(page.get_by_role('heading',name='长流程分析结果')).to_have_count(0)
  sources.get_by_role('button',name='项目文档',exact=True).click()
  expect(page.get_by_text('当前项目还没有 Word、PPT 或 PDF 文档。',exact=True)).to_be_visible()
  sources.get_by_role('button',name='工作区分析',exact=True).click()
  expect(page.get_by_text('工作区还是空的',exact=True)).to_be_visible()
  checks.append('analysis navigation contains only workspace analysis and project documents')
  page.screenshot(path=str(OUT/'analysis-results.png'),full_page=True)
  for width in [1280,900,768]:
   page.set_viewport_size({'width':width,'height':900});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  checks.append('three viewport widths');assert not errors,errors
 finally:
  (OUT/'result.json').write_text(json.dumps({'checks':checks,'pageErrors':errors,'externalRequests':external,'mode':'real React and Edge; synthetic bridge, no actual model or SSH'},ensure_ascii=False,indent=2),encoding='utf-8')
  page.screenshot(path=str(OUT/'final.png'),full_page=True);browser.close()
print(json.dumps({'passed':len(checks),'errors':errors}))
