"""Verify the real settings account entry with isolated payment IPC, never mock plans."""
import json
from payment_test import BROWSER, OUT, guarded_page, open_fixture, sync_playwright, expect


def main():
    checks, errors, external = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=BROWSER, headless=True)
        page = guarded_page(browser, errors, external, width=1280)
        try:
            for role in ['locked', 'qa', 'admin', 'empty']:
                open_fixture(page, role)
                account = page.locator('#settings-tab-account')
                if role == 'locked':
                    expect(account).to_have_count(0)
                    expect(page.get_by_role('heading',name='付款与订单',exact=True)).to_have_count(0)
                    assert page.evaluate('window.__payment.calls.length')==0
                    checks.append('Unauthenticated users cannot access payment settings')
                    continue
                expect(page.get_by_role('button',name='账户与用量',exact=True)).to_have_count(0)
                page.get_by_role('button',name='设置',exact=True).click()
                expect(account).to_contain_text('账号与支付')
                expect(account).to_have_attribute('aria-selected','true')
                assert page.locator('.omaax-settings-nav [role=tab]').first.get_attribute('id')=='settings-tab-account'
                expect(page.get_by_role('heading',name='付款与订单',exact=True)).to_be_visible()
                expect(page.get_by_text('暂无订单记录',exact=True)).to_be_visible()
                panel=page.locator('#settings-pane-account').inner_text()
                for fake in ['基础版','专业版','旗舰版','剩余额度','本月用量','积分（Token）记录','演示数据']:
                    assert fake not in panel,(role,fake)
                expect(page.get_by_role('heading',name='模型使用情况',exact=True)).to_be_visible()
                expect(page.get_by_role('button',name='分析结果',exact=True)).to_be_visible()
                expect(page.get_by_role('button',name='教育科研',exact=True)).to_be_visible()
                if role=='empty':
                    expect(page.get_by_role('button',name='知识库问答',exact=True)).to_have_count(0)
                expect(page.get_by_role('button',name='管理后台',exact=True)).to_have_count(1 if role=='admin' else 0)
                assert page.evaluate("window.__payment.calls.every(c=>c.action==='list')")
                checks.append(role+' has payment access independent of knowledge permission, with no automatic order creation')
                if role=='admin':
                    page.screenshot(path=str(OUT/'account-admin-zh.png'))
                    page.get_by_role('button',name='退出 / 切换账号',exact=True).click()
                    expect(page.get_by_role('form',name='登录 OMaaX',exact=True)).to_be_visible()
                    expect(page.get_by_role('heading',name='付款与订单',exact=True)).to_have_count(0)
                    checks.append('Logout unmounts account and payment state')
            assert not errors,errors
            assert not external,external
        except Exception:
            page.screenshot(path=str(OUT/'account-failure.png'))
            (OUT/'account-failure.html').write_text(page.content(),encoding='utf-8')
            raise
        finally:
            (OUT/'account-result.json').write_text(json.dumps({'checks':checks,'errors':errors,'externalRequests':external},ensure_ascii=False,indent=2),encoding='utf-8')
            browser.close()
    print(json.dumps({'passed':len(checks),'errors':errors},ensure_ascii=False))


if __name__=='__main__':
    main()
