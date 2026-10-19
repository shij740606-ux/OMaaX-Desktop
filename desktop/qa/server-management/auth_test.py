"""Real React/Edge, synthetic hub bridge; never uses real passwords or servers."""
import json,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
OUT=ROOT/'validation-local/auth-browser';OUT.mkdir(exist_ok=True)
checks=[];errors=[]
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=os.environ.get('OMAAX_QA_BROWSER',r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),headless=True)
 page=browser.new_page(viewport={'width':1280,'height':900})
 page.on('pageerror',lambda e:errors.append(str(e)))
 def scenario(name):
  page.goto('http://127.0.0.1:41740/workflow-preview.html?auth='+name)
  page.wait_for_load_state('networkidle')
 try:
  for name in ['locked','offline','disabled']:
   scenario(name)
   expect(page.get_by_role('heading',name='登录 OMaaX')).to_be_visible()
   assert page.evaluate('window.__authQa.businessCalls')==0
   expect(page.get_by_role('button',name='注册',exact=True)).to_have_count(0)
   expect(page.get_by_role('button',name='分析',exact=True)).to_have_count(0)
   checks.append(name+' cannot mount business UI or start agent')
  scenario('locked')
  page.get_by_label('用户名',exact=True).fill('alice');page.get_by_label('密码',exact=True).fill('wrong')
  page.get_by_role('button',name='登录',exact=True).click()
  expect(page.get_by_role('alert')).to_contain_text('用户名或密码错误')
  expect(page.get_by_label('密码',exact=True)).to_have_value('')
  assert page.evaluate('window.__authQa.businessCalls')==0
  checks.append('failed login stays locked and clears password')
  page.get_by_label('密码',exact=True).fill('test-password');page.get_by_role('button',name='登录',exact=True).click()
  expect(page.get_by_role('button',name='退出 / 切换账号')).to_be_visible()
  expect(page.get_by_role('button',name='分析',exact=True)).to_be_visible()
  expect(page.get_by_role('button',name='管理后台',exact=True)).to_have_count(0)
  assert 'test-password' not in page.evaluate('JSON.stringify(localStorage)')
  checks.append('qa account has analysis access but no admin entry or password persistence')
  page.get_by_role('button',name='退出 / 切换账号').click()
  expect(page.get_by_role('heading',name='登录 OMaaX')).to_be_visible()
  assert page.evaluate('window.__authQa.logoutCalls')==1
  checks.append('logout unmounts all business UI')
  scenario('admin');expect(page.get_by_role('button',name='管理后台',exact=True)).to_be_visible()
  expect(page.get_by_role('button',name='分析',exact=True)).to_be_visible()
  expect(page.get_by_role('button',name='知识库问答',exact=True)).to_be_visible()
  page.evaluate('window.__authQa.status.authenticated=false;window.__authQa.status.error="登录已过期"')
  expect(page.get_by_role('heading',name='登录 OMaaX')).to_be_visible(timeout=20000)
  checks.append('expiry removes admin and analysis views')
  scenario('bio');expect(page.get_by_role('button',name='教育科研',exact=True)).to_be_visible()
  expect(page.get_by_role('button',name='知识库问答',exact=True)).to_have_count(0)
  checks.append('ordinary account has research access; bioflow does not imply cloud QA')
  scenario('team');page.get_by_role('button',name='教育科研',exact=True).click()
  expect(page.get_by_role('note')).to_have_count(0)
  expect(page.get_by_role('button',name='重新连接',exact=True)).to_be_visible()
  checks.append('logged-in account opens research without explanatory banner and retains connection retry')
  scenario('empty')
  for name in ['分析','教育科研','新对话']:
   expect(page.get_by_role('button',name=name,exact=True)).to_be_visible()
  expect(page.get_by_role('button',name='知识库问答',exact=True)).to_have_count(0)
  expect(page.get_by_role('button',name='管理后台',exact=True)).to_have_count(0)
  checks.append('ordinary account without entitlements has standard features but no knowledge/admin entry')
  scenario('qa-new');expect(page.get_by_text('欢迎使用 OMaaX',exact=True)).to_be_visible()
  checks.append('new ordinary account can configure models and analysis environment')
  scenario('load-error');expect(page.get_by_role('button',name='重新加载',exact=True)).to_be_visible()
  page.get_by_role('button',name='重新加载',exact=True).click();expect(page.get_by_role('button',name='分析',exact=True)).to_be_visible()
  checks.append('transient initialization failure shows retry and recovers')
  scenario('admin');page.get_by_role('button',name='设置',exact=True).click()
  for tab in ['对话模型','工具与服务','文件与软件','界面偏好','关于 OMaaX']:
   page.get_by_role('tab',name=tab).click()
   visible=page.locator('body').inner_text()
   assert not any(word in visible for word in ['中枢','旧版','新版','MCP','AUTH_ENABLED','内核'])
  page.get_by_role('tab',name='对话模型').click();page.get_by_role('button',name='本地／内网模型',exact=True).click()
  expect(page.get_by_text('已有助手服务地址（可选）',exact=True)).to_have_count(0)
  checks.append('all settings panes use user-facing text and omit disabled connection forms')
  page.clock.install();scenario('admin')
  expect(page.get_by_role('button',name='退出 / 切换账号')).to_be_visible()
  page.evaluate("()=>{window.biodsh.hubStatus=()=>new Promise(resolve=>{const snapshot=JSON.parse(JSON.stringify(window.__authQa.status));window.__releaseStatus=()=>resolve(snapshot)});}")
  page.clock.fast_forward(15001);page.wait_for_function('typeof window.__releaseStatus==="function"')
  page.get_by_role('button',name='退出 / 切换账号').click();page.evaluate('window.__releaseStatus()')
  expect(page.get_by_role('heading',name='登录 OMaaX')).to_be_visible();expect(page.get_by_role('button',name='分析',exact=True)).to_have_count(0)
  checks.append('late status response cannot restore business UI after logout')
  scenario('locked')
  expect(page.get_by_label('服务地址',exact=True)).to_have_count(0)
  assert 'https://cloud.haiguji.com' not in page.locator('body').inner_text()
  assert not any(word in page.locator('body').inner_text() for word in ['中枢','旧版','新版','AUTH_ENABLED'])
  checks.append('login terminology explains usage without backend details')
  scenario('locked&remember=1')
  expect(page.get_by_label('用户名',exact=True)).to_have_value('remembered-user')
  expect(page.get_by_label('密码',exact=True)).to_have_value('test-password')
  expect(page.get_by_label('密码',exact=True)).to_have_attribute('type','password')
  expect(page.get_by_label('记住账号和密码',exact=True)).to_be_checked()
  assert page.evaluate('window.__authQa.loginCalls')==0
  expect(page.get_by_role('button',name='分析',exact=True)).to_have_count(0)
  assert 'test-password' not in page.evaluate('JSON.stringify(localStorage)')
  page.get_by_label('记住账号和密码',exact=True).uncheck()
  assert page.evaluate('window.__authQa.forgotten')
  page.get_by_role('button',name='登录',exact=True).click()
  expect(page.get_by_role('button',name='分析',exact=True)).to_be_visible()
  checks.append('remembered masked credentials never auto-login; uncheck clears secure record; explicit login unlocks')
  for width,height in [(1280,900),(760,600),(480,420)]:
   page.set_viewport_size({'width':width,'height':height});scenario('qa-new')
   page.evaluate("window.biodsh.setSettings=async patch=>({...await window.biodsh.getSettings(),...patch})")
   overlay=page.get_by_test_id('onboarding');expect(overlay).to_be_visible()
   box=overlay.bounding_box();assert box=={'x':0,'y':0,'width':width,'height':height},box
   assert overlay.locator('.card').count()==0
   if width==1280:page.screenshot(path=str(OUT/'onboarding-full-window.png'))
   for label in ['开始','下一步','下一步','进入 OMaaX']:
    page.get_by_role('button',name=label,exact=True).click()
   expect(overlay).to_have_count(0)
  checks.append('first-use wizard fills entire window at three sizes and all steps remain usable')
  scenario('locked')
  for width in [1280,900,768]:
   page.set_viewport_size({'width':width,'height':900});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  page.screenshot(path=str(OUT/'login.png'),full_page=True)
  assert not errors,errors
 finally:
  (OUT/'result.json').write_text(json.dumps({'checks':checks,'errors':errors,'mode':'real React/Edge, synthetic bridge; native auth tested separately'},ensure_ascii=False,indent=2),encoding='utf-8')
  browser.close()
print(json.dumps({'passed':len(checks),'errors':errors}))
