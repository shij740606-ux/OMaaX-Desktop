"""Three-mode conversation UI against a synthetic bridge; no model charges."""
import json
import sys
from urllib.parse import urlsplit, parse_qs
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('http://127.0.0.1:41740/workflow-preview.html?model=1&reasoning=1')
    page.get_by_role('article').filter(has=page.get_by_role('heading', name='RNA-seq 分析', exact=True)).get_by_role('button').click()
    page.get_by_role('button', name='交给模型配置与分析', exact=True).click()
    modes = page.get_by_label('推理模式', exact=True)
    expect(modes).to_have_value('high')
    assert modes.locator('option').all_text_contents() == ['低', '中', '高']
    def mode_navigation(route):
        params = parse_qs(urlsplit(route.request.url).query)
        route.fulfill(status=204)
        page.evaluate("e=>window.__modeRequest(e)", {'type':'reasoning-request','sessionId':params['session'][0],'effort':params['effort'][0]})
    page.route('**/__omaax_mode?*', mode_navigation)
    for value in ['low', 'max', 'high']:
        modes.select_option(value)
        expect(modes).to_be_enabled()
        expect(modes).to_have_value(value)
    calls = page.evaluate("window.__qa.calls.filter(c=>c.action==='session.reasoningSet')")
    assert [c['body']['effort'] for c in calls] == ['low', 'max', 'high']
    assert all(c['body']['sessionId'] == 'preview-session' for c in calls)
    page.evaluate("window.__modeRequest({type:'reasoning-request',sessionId:'other-session',effort:'max'})")
    expect(modes).to_have_value('high')
    assert page.locator('header').get_by_label('推理模式', exact=True).count()==0
    assert modes.locator('xpath=ancestor::*[@data-composer-card]').count()==1
    page.evaluate("""document.body.insertAdjacentHTML('beforeend', '<div data-chat-flow-kind="system-prompt"><details open><summary>系统提示词</summary>INTERNAL_SECRET</details></div>')""")
    expect(page.locator('[data-chat-flow-kind="system-prompt"]')).not_to_be_visible()
    assert 'INTERNAL_SECRET' not in page.locator('body').inner_text()
    assert not errors, errors
    page.screenshot(path=str(ROOT/'validation-local/reasoning-three-modes.png'))
    browser.close()
print(json.dumps({'passed': 4, 'errors': [], 'checks': ['three labels', 'server default', 'all selections', 'session scope']}))
