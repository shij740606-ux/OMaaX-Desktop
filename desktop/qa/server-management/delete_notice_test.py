"""Browser visibility acceptance against synthetic operations only."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
 page=browser.new_page(viewport={'width':1100,'height':800});errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:41741/delete-notice-preview.html?offscreen');page.wait_for_load_state('networkidle')
 notice=page.get_by_role('region',name='删除提醒')
 expect(page.get_by_text('/test-only/result.tmp',exact=True)).to_have_count(1)
 page.wait_for_timeout(5500)
 assert page.evaluate("window.__noticeCalls.filter(c=>c.action==='server_delete_notified').length")==0
 notice.scroll_into_view_if_needed()
 expect(page.get_by_text('7 字节（0.00 MiB）',exact=False)).to_be_visible()
 page.screenshot(path=str(ROOT/'validation-local/delete-notice-visible.png'))
 expect(page.get_by_text('已移入回收站，可恢复：',exact=False)).to_be_visible(timeout=9000)
 assert page.evaluate("window.__noticeCalls.filter(c=>c.action==='server_delete_notified').length")==1
 assert page.evaluate("window.__noticeCalls.find(c=>c.action==='server_delete_notified').body")=={'id':'test','digest':'snapshot'}
 page.reload();page.wait_for_load_state('networkidle');notice.scroll_into_view_if_needed()
 page.get_by_role('button',name='取消删除',exact=True).click()
 page.wait_for_timeout(5500)
 assert page.evaluate("window.__noticeCalls.filter(c=>c.action==='server_delete_notified').length")==0
 assert page.evaluate("window.__noticeCalls.filter(c=>c.action==='server_delete_reject').length")==1
 assert not errors,errors
 browser.close()
print(json.dumps({'passed':4,'checks':['offscreen never executes','visible scoped receipt','once only','cancel prevents execution']}))
