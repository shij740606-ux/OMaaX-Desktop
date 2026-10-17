"""Exercise merged account UI behind the real React auth gate with synthetic credentials."""
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect

out = ROOT / 'validation-local/account-merge-browser'
out.mkdir(exist_ok=True)
checks, errors = [], []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('OMAAX_QA_BROWSER', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'), headless=True)
    page = browser.new_page(viewport={'width':1280, 'height':1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        for role in ['locked', 'qa', 'admin', 'empty']:
            page.goto('http://127.0.0.1:41740/workflow-preview.html?auth=' + role)
            page.wait_for_load_state('networkidle')
            account = page.get_by_role('button', name='账户与用量', exact=True)
            if role == 'locked':
                expect(account).to_have_count(0)
                checks.append('account page unavailable before login')
                continue
            expect(account).to_be_visible()
            account.click()
            expect(page.get_by_role('status')).to_contain_text('数据不代表实际账单')
            expect(page.get_by_text('本月用量', exact=True)).to_be_visible()
            expect(page.get_by_text('积分（Token）记录', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='知识库', exact=True)).to_have_count(0)
            if role != 'admin':
                expect(page.get_by_role('button', name='分析', exact=True)).to_have_count(0)
            checks.append(role + ' retains account UI and entitlement restrictions')
            if role != 'admin':
                continue
            page.get_by_role('button', name='选择 基础版', exact=True).click()
            expect(page.get_by_role('button', name='已选择', exact=True)).to_be_visible()
            for channel in ['微信支付', '支付宝']:
                page.get_by_role('button', name=channel, exact=True).click()
                expect(page.get_by_text('支付服务接入云端后，这里会显示收款二维码。当前为演示数据。', exact=True)).to_be_visible()
                page.get_by_role('button', name='取消支付', exact=True).click()
                expect(page.get_by_role('button', name='取消支付', exact=True)).to_have_count(0)
            checks.append('plan selection and both payment previews preserved')
            for theme in ['light', 'dark']:
                page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
                for width in [1280, 900, 768]:
                    page.set_viewport_size({'width':width, 'height':1000})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.get_by_role('status').scroll_into_view_if_needed()
                page.screenshot(path=str(out / (theme + '.png')), full_page=True)
            checks.append('light/dark layouts at three viewport widths')
            page.get_by_role('button', name='退出 / 切换账号').click()
            expect(page.get_by_role('heading', name='登录 OMaaX')).to_be_visible()
            expect(page.get_by_text('本月用量', exact=True)).to_have_count(0)
            checks.append('logout unmounts account and payment UI')
        assert not errors, errors
    finally:
        (out / 'result.json').write_text(json.dumps({'checks':checks, 'errors':errors}, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
print(json.dumps({'passed':len(checks), 'errors':errors}))
