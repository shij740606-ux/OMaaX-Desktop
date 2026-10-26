"""Company model UI; no real account or upstream calls."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
 page=browser.new_page(viewport={'width':1280,'height':900});errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:41740/workflow-preview.html?auth=qa');page.wait_for_load_state('networkidle')
 page.get_by_role('button',name='设置',exact=True).click()
 expect(page.get_by_role('tab',name='对话服务')).to_have_count(0)
 expect(page.locator('#settings-pane-model')).to_have_count(0)
 expect(page.get_by_role('tab',name='账号与支付')).to_have_attribute('aria-selected','true')
 page.screenshot(path=str(ROOT/'validation-local/company-llm-settings.png'))
 page.evaluate("()=>{window.__balanceCalls=0;window.biodsh.deepseekBalance=async()=>{window.__balanceCalls++;throw Error('must not call');};}")
 page.get_by_role('tab',name='账号与支付').click();expect(page.get_by_text('此处为本机保存的对话用量，不代表账号累计用量或余额。')).to_be_visible()
 assert page.evaluate('window.__balanceCalls')==0
 page.get_by_role('tab',name='工具与服务').click();expect(page.get_by_label('服务访问密钥',exact=True)).to_be_visible()
 for width in [760,1024,1440]:
  page.set_viewport_size({'width':width,'height':900});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
 assert not errors,errors
 browser.close()
print(json.dumps({'passed':6,'errors':errors,'checks':['company service','no personal key','no provider labels','no balance calls','image config preserved','responsive']}))
