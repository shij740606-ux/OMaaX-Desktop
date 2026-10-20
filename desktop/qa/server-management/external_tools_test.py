"""Settings restoration in real React; isolated configuration and synthetic bridge."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
 page=browser.new_page(viewport={'width':1280,'height':1000});errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:41740/workflow-preview.html?auth=qa');page.wait_for_load_state('networkidle')
 page.evaluate("""()=>{window.__toolSaves=[];window.__toolRestarts=0;const get=window.biodsh.getSettings;let state;window.biodsh.setSettings=async patch=>{state={...(state||await get()),...patch};window.__toolSaves.push(patch);return state;};window.biodsh.dshRestart=async()=>{window.__toolRestarts++;return {state:'running'};};}""")
 page.get_by_role('button',name='设置',exact=True).click();page.get_by_role('tab',name='工具与服务').click()
 expect(page.get_by_text('图像生成与外部应用连接暂不可用。')).to_have_count(0)
 page.get_by_label('服务接口地址',exact=False).fill('https://example.test/v1')
 page.get_by_label('图像模型',exact=True).fill('test-image')
 page.get_by_label('服务访问密钥',exact=True).fill('synthetic-key')
 page.get_by_role('button',name='保存图像连接',exact=True).click()
 page.wait_for_function('window.__toolSaves.length===1')
 assert page.evaluate('window.__toolSaves[0].imageModel')=='test-image'
 page.get_by_role('button',name='已有工具服务',exact=False).click()
 page.get_by_label('服务地址',exact=True).fill('http://127.0.0.1:3010/mcp')
 page.get_by_role('button',name='保存工具连接',exact=True).click()
 page.wait_for_function('window.__toolSaves.length===2')
 assert page.evaluate('window.__toolSaves[1].mcpServers[0].url')=='http://127.0.0.1:3010/mcp'
 page.get_by_role('button',name='PubMed 文献检索',exact=False).click()
 args=page.get_by_label('启动参数',exact=True);args.fill('["C:/folder with spaces/tool.js", "--port", "3000"]')
 page.get_by_label('环境变量（JSON 对象）',exact=True).fill('{"KEY":"value with spaces"}')
 page.get_by_role('button',name='保存工具连接',exact=True).click()
 page.wait_for_function('window.__toolSaves.length===3')
 assert page.evaluate('window.__toolSaves[2].mcpServers[1].args[0]')=='C:/folder with spaces/tool.js'
 assert page.evaluate('window.__toolSaves[2].mcpServers[1].env.KEY')=='value with spaces'
 args.fill('invalid JSON');page.get_by_role('button',name='保存工具连接',exact=True).click(force=True)
 assert page.evaluate('window.__toolSaves.length')==3
 args.fill('["fixed"]')
 page.evaluate("()=>{window.biodsh.setSettings=async()=>{throw Error('synthetic failure')};}")
 page.get_by_role('button',name='保存工具连接',exact=True).click()
 expect(page.get_by_role('alert')).to_contain_text('保存或连接失败')
 page.get_by_role('button',name='移除此连接',exact=True).first.click()
 expect(page.get_by_label('启动参数',exact=True)).to_have_value('["fixed"]')
 expect(page.get_by_label('环境变量（JSON 对象）',exact=True)).to_have_value('{"KEY":"value with spaces"}')
 for width in [1280,900,768]:
  page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
 page.screenshot(path=str(ROOT/'validation-local/external-tools-settings.png'),full_page=True)
 assert not errors,errors
 browser.close()
print(json.dumps({'passed':7,'errors':errors}))
