"""Knowledge chat layout and both submission paths, with synthetic cloud data."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
out=ROOT/'validation-local/knowledge-layout';out.mkdir(exist_ok=True)
checks=[];errors=[]
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
 page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
 try:
  page.goto('http://127.0.0.1:41740/workflow-preview.html?auth=qa&cloud=1');page.wait_for_load_state('networkidle')
  page.get_by_role('button',name='知识库问答',exact=True).click()
  q=page.get_by_label('科研问题',exact=True)
  ask=page.get_by_role('button',name='提问',exact=True);search=page.get_by_role('button',name='仅检索文献',exact=True)
  expect(ask).to_be_disabled();expect(search).to_be_disabled()
  page.get_by_role('button',name='水稻 SPL 基因家族有哪些主要功能？',exact=True).click()
  expect(q).to_be_focused();expect(ask).to_be_enabled();expect(search).to_be_enabled()
  assert page.evaluate("window.__cloudQa.calls.filter(a=>a==='chat'||a==='search').length")==0
  for width,height in [(1440,900),(1024,704),(760,600)]:
   page.set_viewport_size({'width':width,'height':height})
   area=page.get_by_role('region',name='问答结果');a=area.bounding_box()
   for suggestion in page.locator('.knowledge-suggestions button').all():
    box=suggestion.bounding_box();assert box['y']>=a['y'] and box['y']+box['height']<=a['y']+a['height'],(width,height,box,a)
   expect(q).to_be_in_viewport();expect(ask).to_be_in_viewport();expect(search).to_be_in_viewport()
   # A few pixels of bottom padding can scroll after the project header wraps;
   # the actionable suggestions above and the composer must still fit completely.
   assert area.evaluate('(e)=>e.scrollHeight-e.clientHeight<=parseFloat(getComputedStyle(e).paddingBottom)+1'),(width,height)
   assert page.locator('.knowledge-composer-area').bounding_box()['height']<height*.3
   page.screenshot(path=str(out/f'compact-empty-{width}.png'))
  checks.append('all empty-state suggestions and composer fit at desktop, scaled and short sizes; only bottom padding may scroll')
  page.set_viewport_size({'width':1440,'height':900})
  page.screenshot(path=str(out/'empty-light.png'))
  checks.append('example fills question; typing enables both buttons without submitting')
  q.press('Shift+Enter');assert '\n' in q.input_value()
  page.evaluate("document.querySelector('#knowledge-question').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true}))")
  assert page.evaluate("window.__cloudQa.calls.filter(a=>a==='chat').length")==0
  q.fill('水稻 SPL 功能');q.press('Enter')
  expect(page.get_by_text('水稻 SPL 参与株型与开花调控。[DOI:10.1/test]',exact=True)).to_be_visible()
  expect(q).to_have_value('');expect(q).to_be_focused()
  checks.append('Enter asks; Shift+Enter adds newline; IME composition does not submit')
  q.fill('文献关键词');search.click();expect(page.get_by_text('找到 2 条文献来源。',exact=True)).to_be_visible()
  assert page.evaluate("window.__cloudQa.calls.filter(a=>a==='search').length")==1
  assert page.evaluate("window.__cloudQa.calls.filter(a=>a==='chat').length")==1
  checks.append('ask and search invoke distinct backend routes')
  page.evaluate("""()=>{const original=window.biodsh.cloudQa;window.biodsh.cloudQa=(a,b)=>a==='chat'?Promise.resolve({answer:Array.from({length:55},(_,i)=>'第 '+i+' 段：文献证据与研究方法。').join('\\n\\n'),sources:[]}):original(a,b)}""")
  q.fill('长回答排版');ask.click();expect(page.get_by_text('第 54 段：文献证据与研究方法。',exact=True)).to_be_visible()
  for theme in ['light','dark']:
   page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
   for width,height in [(1440,1000),(1024,768),(760,600)]:
    page.set_viewport_size({'width':width,'height':height})
    area=page.get_by_role('region',name='问答结果');footer=page.locator('.knowledge-composer-area')
    a=area.bounding_box();f=footer.bounding_box();assert a['y']+a['height']<=f['y']+1
    assert f['y']+f['height']<=height+1
    expect(q).to_be_in_viewport();expect(ask).to_be_in_viewport();expect(search).to_be_in_viewport()
    old=footer.bounding_box()['y'];area.evaluate('(e)=>e.scrollTop=0');assert abs(footer.bounding_box()['y']-old)<1
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
   page.set_viewport_size({'width':1440,'height':1000})
   page.screenshot(path=str(out/('answer-'+theme+'.png')))
  checks.append('result pane scrolls independently; composer stays below at three sizes in light/dark')
  assert not errors,errors
 finally:
  (out/'result.json').write_text(json.dumps({'checks':checks,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
  browser.close()
print(json.dumps({'passed':len(checks),'errors':errors}))
