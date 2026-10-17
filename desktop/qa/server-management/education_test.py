"""React shell + migrated Vue workspace. OMAAX_EDUCATION_URL is a live isolated bridge URL."""
import json,os,sys
from pathlib import Path
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
out=ROOT/'validation-local/education-browser';out.mkdir(exist_ok=True)
checks=[];errors=[]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
 page=b.new_page(viewport={'width':1440,'height':1000},accept_downloads=True);page.on('pageerror',lambda e:errors.append(str(e)))
 try:
  query=urlencode({'auth':'local','eduUrl':os.environ['OMAAX_EDUCATION_URL']})
  page.goto('http://127.0.0.1:41740/workflow-preview.html?'+query);page.wait_for_load_state('networkidle')
  expect(page.get_by_role('heading',name='登录 OMaaX')).to_have_count(0)
  expect(page.get_by_role('button',name='退出 / 切换账号')).to_have_count(0)
  page.get_by_role('button',name='教育科研',exact=True).click();frame=page.frame_locator('iframe[title="教育科研工作台"]')
  expect(frame.locator('tbody tr').first).to_be_visible(timeout=30000);checks.append('local access opens real education from sidebar without account')
  frame.get_by_role('tab',name='🇨🇳 中文期刊',exact=True).click();expect(frame.locator('tbody tr').first).to_be_visible()
  search=frame.get_by_placeholder('搜索期刊名（如 Nature、Plant Cell）');search.fill('OMaaX-no-such-journal-xyz')
  expect(frame.locator('tbody tr')).to_have_count(0,timeout=10000);search.fill('');expect(frame.locator('tbody tr').first).to_be_visible();checks.append('journal language and search filters')
  frame.get_by_role('button',name='智能投稿推荐',exact=True).click();abstract=frame.locator('textarea');text='This synthetic test manuscript describes plant transcriptome analysis under drought stress. Six simulated samples illustrate an expression workflow without biological claims.'
  abstract.fill(text);page.get_by_role('button',name='账户与用量',exact=True).click();page.get_by_role('button',name='教育科研',exact=True).click();expect(abstract).to_have_value(text);checks.append('draft persists across desktop navigation')
  frame.get_by_role('button',name='开始推荐',exact=True).click();expect(frame.get_by_role('tablist',name='档位筛选')).to_be_visible(timeout=120000);checks.append('real recommendation through embedded page')
  frame.get_by_role('button',name='论文排版与规范',exact=True).click();frame.locator('input[type=file]').set_input_files(str(ROOT/'validation-local/education-api-live/synthetic.pdf'))
  frame.get_by_role('button',name='开始版式检查',exact=True).click();expect(frame.locator('.fc-summary')).to_be_visible(timeout=30000);checks.append('PDF upload and format inspection')
  frame.get_by_role('button',name='论文自动重排版',exact=True).click()
  tex=b'\\documentclass[10pt]{article}\n\\begin{document}\nSynthetic integration test only.\n\\end{document}'
  frame.locator('input[type=file]').set_input_files({'name':'synthetic.tex','mimeType':'text/plain','buffer':tex})
  frame.get_by_role('button',name='开始自动重排版',exact=True).click();expect(frame.get_by_role('button',name='下载修改后源码',exact=True)).to_be_visible(timeout=30000)
  with page.expect_download() as download:frame.get_by_role('button',name='下载修改后源码',exact=True).click()
  download.value.save_as(str(out/'reformatted.tex'));assert b'documentclass'in (out/'reformatted.tex').read_bytes();checks.append('TeX upload, reformat and download')
  for label in ['AI 预审稿系统','专业审稿意见报告','审稿回复助手','会议与投稿日历','学术期刊情报']:
   frame.get_by_role('button',name=label,exact=True).click();page.wait_for_timeout(300)
  checks.append('all eight panels navigate without page errors')
  for width in [1440,1000,768]:
   page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  page.screenshot(path=str(out/'education.png'),full_page=True);assert not errors,errors
 finally:
  (out/'result.json').write_text(json.dumps({'checks':checks,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8');b.close()
print(json.dumps({'passed':len(checks),'errors':errors}))
