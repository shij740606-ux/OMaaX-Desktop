"""Browser regression for cached education panels; uses synthetic API replies only.

Start Vite with vite.education.config.mjs on port 5187, then run this file.
No credentials, model calls, uploaded user files or production services are used.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'validation-local/qa-runtime/python'))
from playwright.sync_api import expect, sync_playwright

OUT = ROOT / 'validation-local/education-navigation'
OUT.mkdir(parents=True, exist_ok=True)
URL = os.environ.get('OMAAX_EDUCATION_TEST_URL', 'http://127.0.0.1:5187/index.html')


def main():
    counts = Counter()
    checks = []
    errors = []
    console_errors = []
    request_failures = []
    fail_next_calendar = False

    def api(route):
        nonlocal fail_next_calendar
        request = route.request
        parsed = urlparse(request.url)
        endpoint = parsed.path.split('/api/review/', 1)[1]
        query = parse_qs(parsed.query, keep_blank_values=True)
        counts[endpoint] += 1
        assert request.method == 'GET', f'Unexpected API mutation: {request.method} {endpoint}'
        if endpoint == 'journals/directory':
            payload = {'ok': True, 'items': [{'id': 'test-journal', 'name': 'Synthetic Plant Journal',
                       'lang': query.get('lang', ['en'])[0], 'research_direction': 'Plants', 'cas_partition': 1}],
                       'meta': {'total': 1, 'page': 1, 'size': 50, 'lang': query.get('lang', ['en'])[0],
                                'domains': ['Plants'], 'data_imported': True, 'data_source_note': 'Synthetic test only'}}
        elif endpoint == 'conferences':
            if fail_next_calendar:
                fail_next_calendar = False
                route.fulfill(status=503, json={'ok': False, 'error': {'code': 'UNAVAILABLE', 'message': 'Synthetic retry test'}})
                return
            payload = {'ok': True, 'items': [{'id': 'test-conference', 'name': 'Synthetic Conference',
                       'full_name': 'Synthetic conference fixture', 'domain_tags': ['植物/农学'],
                       'application_tags': [], 'ccf_rank': 'A', 'deadline_full_paper': '2099-12-31',
                       'deadline_source': 'placeholder', 'deadline_confidence': 'low'}], 'meta': {'count': 1}}
        elif endpoint == 'journals':
            payload = {'ok': True, 'items': [{'id': 'test-journal', 'name': 'Synthetic Plant Journal'}], 'meta': {'count': 1}}
        elif endpoint == 'ppt/styles':
            payload = {'ok': True, 'items': []}
        else:
            raise AssertionError(f'Unexpected API read: {endpoint}')
        route.fulfill(json=payload)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
        page.on('requestfailed', lambda request: request_failures.append({'url': request.url, 'error': request.failure}))
        page.route('**/api/review/**', api)
        try:
            page.goto(URL)
            page.wait_for_load_state('networkidle')
            expect(page.locator('tbody tr')).to_have_count(1)
            search = page.get_by_placeholder('搜索期刊名（如 Nature、Plant Cell）')
            search.fill('Plant')
            expect(page.locator('tbody tr')).to_have_count(1)
            page.wait_for_timeout(400)  # Wait for the explicit 300 ms search debounce.
            page.wait_for_load_state('networkidle')
            directory_calls = counts['journals/directory']
            assert directory_calls == 2, dict(counts)

            def nav(label):
                page.get_by_role('button', name=label, exact=True).click()

            nav('会议与投稿日历')
            expect(page.locator('.cl-card')).to_have_count(1)
            expect(page.locator('.cl-filters select').first.locator('option')).to_have_count(2)
            page.locator('.cl-filters select').first.select_option('植物/农学')
            page.wait_for_load_state('networkidle')
            page.locator('.cl-filters select').nth(1).select_option('A')
            page.wait_for_load_state('networkidle')
            calendar_calls = counts['conferences']
            assert calendar_calls == 3, dict(counts)
            nav('学术期刊情报')
            expect(search).to_have_value('Plant')
            page.wait_for_load_state('networkidle')
            assert counts['journals/directory'] == directory_calls
            checks.append('Journal query, list and request count persist across section changes')
            nav('会议与投稿日历')
            expect(page.locator('.cl-filters select').first).to_have_value('植物/农学')
            expect(page.locator('.cl-filters select').nth(1)).to_have_value('A')
            page.wait_for_load_state('networkidle')
            assert counts['conferences'] == calendar_calls
            checks.append('Calendar filters persist and reopening does not refetch')

            # A genuine user filter change still requests fresh data; retry is never cached as success.
            fail_next_calendar = True
            page.locator('.cl-filters input[type=checkbox]').uncheck()
            expect(page.get_by_role('alert')).to_contain_text('Synthetic retry test')
            failed_calls = counts['conferences']
            nav('学术期刊情报')
            nav('会议与投稿日历')
            expect(page.get_by_role('alert')).to_be_visible()
            assert counts['conferences'] == failed_calls
            page.get_by_role('button', name='重试', exact=True).click()
            expect(page.locator('.cl-card')).to_have_count(1)
            assert counts['conferences'] == failed_calls + 1
            expect(page.locator('.cl-filters input[type=checkbox]')).not_to_be_checked()
            checks.append('Explicit retry after API failure succeeds and preserves active filters')

            nav('论文排版与规范')
            page.locator('input[type=file]').set_input_files({'name': 'keep-selected.tex', 'mimeType': 'text/plain',
                'buffer': b'\\documentclass{article}\\begin{document}Synthetic test\\end{document}'})
            expect(page.locator('.fc-file-name')).to_contain_text('keep-selected.tex')
            nav('审稿回复助手')
            draft = 'Reviewer 1: Preserve this unsubmitted review draft when changing sections.'
            page.locator('#rb-text').fill(draft)
            nav('论文排版与规范')
            expect(page.locator('.fc-file-name')).to_contain_text('keep-selected.tex')
            expect(page.get_by_role('button', name='开始版式检查', exact=True)).to_be_enabled()
            assert counts['journals'] == 1
            checks.append('Selected format file survives navigation without a second rules request')
            nav('审稿回复助手')
            expect(page.locator('#rb-text')).to_have_value(draft)
            checks.append('Unsubmitted reviewer draft survives navigation')

            for label in ['智能投稿推荐', 'AI 预审稿系统', '专业审稿意见报告', '论文自动重排版', '科研汇报助手']:
                nav(label)
            nav('学术期刊情报')
            expect(search).to_have_value('Plant')
            nav('会议与投稿日历')
            expect(page.locator('.cl-filters select').first).to_have_value('植物/农学')
            page.wait_for_load_state('networkidle')
            assert counts['journals/directory'] == directory_calls
            assert counts['journals'] == 1
            assert counts['conferences'] == failed_calls + 1
            assert not errors, errors
            # One 503 is deliberately returned above to exercise the visible retry flow.
            unexpected_console = [message for message in console_errors if '503 (Service Unavailable)' not in message]
            assert not unexpected_console, unexpected_console
            assert not request_failures, request_failures
            checks.append('All nine sections navigate without page errors or extra catalog requests')
            page.screenshot(path=str(OUT / 'calendar-kept.png'), full_page=True)
        finally:
            (OUT / 'result.json').write_text(json.dumps({'checks': checks, 'api_calls': dict(counts),
                'page_errors': errors, 'console_errors': console_errors,
                'request_failures': request_failures}, ensure_ascii=False, indent=2), encoding='utf-8')
            browser.close()
    print(json.dumps({'passed': len(checks), 'api_calls': dict(counts), 'page_errors': errors}, ensure_ascii=False))


if __name__ == '__main__':
    main()
