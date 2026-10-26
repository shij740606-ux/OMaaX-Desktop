"""Full styled renderer check for text editing, deletion and transparent shapes."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('OMAAX_QA_URL', 'http://127.0.0.1:41758').rstrip('/')
OUT = ROOT / 'provenance/pending/knowledge-retention-057/figure-browser'
OUT.mkdir(parents=True, exist_ok=True)
checks, errors, external = [], [], []

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
    page = browser.new_page(viewport={'width':1440, 'height':1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith((BASE+'/', BASE.replace('http:', 'ws:')+'/', 'data:', 'blob:')) else (external.append(route.request.url), route.abort()))
    try:
        page.goto(BASE+'/workflow-preview.html?auth=qa&cloud=1')
        page.wait_for_load_state('networkidle')
        expect(page.get_by_role('button', name='科研绘图', exact=True)).to_be_visible()
        page.evaluate("""async()=>{
          const m=await import('/src/i18n.ts');m.syncLanguage('en');
          window.__figureExports=[];
          const old=window.biodsh.dshRpc;
          window.biodsh.dshRpc=async(method,body)=>method==='workspace.list'?{items:[{workspaceId:'p',path:'C:/fixture/figure',title:'Figure project',sessionIds:[]}],archivedSessionIds:[]}:old(method,body);
          window.biodsh.drawing=async(action,body)=>{if(action==='figure_export'){window.__figureExports.push(body);return {path:'C:/fixture/figure/export.'+body.format};}throw Error('Unexpected native drawing call: '+action);};
        }""")
        page.get_by_role('button', name='Scientific drawing', exact=True).click()
        page.get_by_role('button', name='Figure composition & refinement', exact=True).click()
        editor = page.get_by_role('region', name='Figure composition & refinement', exact=True)
        page.get_by_role('button', name='Add text', exact=True).click()
        text = page.get_by_label('Text content', exact=True)
        expect(text).to_be_focused()
        text.fill('Panel A\nYABBY activity')
        expect(page.locator('.figure-canvas text')).to_have_text('Panel AYABBY activity')
        page.wait_for_function("()=>{const g=document.querySelector('.figure-canvas [data-figure-id]'),text=g.querySelector('text').getBBox(),rect=g.querySelector('rect');return Number(rect.getAttribute('height'))>=text.y+text.height;}")
        text.press('Home')
        text.press('Delete')
        expect(page.locator('.figure-canvas [data-figure-id]')).to_have_count(1)
        text.fill('Panel A\nYABBY activity')
        page.screenshot(path=str(OUT/'text-editing-en.png'), animations='disabled')
        checks.append('Adding text focuses the visible editor; multiline updates reach the canvas; Delete inside text edits text instead of deleting its layer')

        page.get_by_role('button', name='Add rectangle', exact=True).click()
        expect(page.locator('.figure-canvas [data-figure-id]')).to_have_count(2)
        rectangle = page.locator('.figure-canvas [data-figure-id]').last.locator('rect[stroke-width="1.5"]')
        expect(rectangle).to_have_attribute('fill', 'none')
        no_fill = page.get_by_label('No fill', exact=True)
        expect(no_fill).to_be_checked()
        no_fill.uncheck()
        expect(rectangle).to_have_attribute('fill', '#e5f3ee')
        no_fill.check()
        expect(rectangle).to_have_attribute('fill', 'none')
        page.get_by_role('button', name='Export SVG', exact=True).last.click()
        page.wait_for_function('window.__figureExports.length===1')
        exported = page.evaluate('window.__figureExports[0]')
        assert exported['format'] == 'svg' and 'fill="none"' in exported['svg']
        assert 'YABBY activity' in exported['svg'] and 'stroke-dasharray' not in exported['svg']
        checks.append('New rectangles are transparent; No fill toggles correctly; SVG export retains text and transparent shape without selection decorations')

        page.locator('.figure-inspector').evaluate('(el)=>el.scrollTop=0')
        page.locator('.figure-editor').evaluate('(el)=>el.scrollTop=0')
        page.screenshot(path=str(OUT/'transparent-rectangle-en-1440.png'), animations='disabled')
        page.get_by_role('button', name='Delete selected elements', exact=True).click()
        expect(page.locator('.figure-canvas [data-figure-id]')).to_have_count(1)
        page.get_by_role('button', name='Undo', exact=True).last.click()
        expect(page.locator('.figure-canvas [data-figure-id]')).to_have_count(2)
        page.locator('.figure-layer-name').filter(has_text='Text').click()
        expect(text).to_have_value('Panel A\nYABBY activity')
        page.get_by_label('Figure canvas', exact=True).focus()
        page.get_by_label('Figure canvas', exact=True).press('Delete')
        expect(page.locator('.figure-canvas [data-figure-id]')).to_have_count(1)
        page.get_by_role('button', name='Undo', exact=True).last.click()
        expect(page.locator('.figure-canvas [data-figure-id]')).to_have_count(2)
        checks.append('Delete selected elements and canvas Delete remove only selected layers; Undo restores them')

        for width, height in [(1440,1000),(768,850)]:
            page.set_viewport_size({'width':width,'height':height})
            page.locator('.figure-editor').evaluate('(el)=>el.scrollTop=0')
            expect(page.get_by_role('button', name='Add text', exact=True)).to_be_in_viewport()
            expect(page.get_by_role('button', name='Add rectangle', exact=True)).to_be_in_viewport()
            expect(page.get_by_role('button', name='Add label', exact=True)).to_be_in_viewport()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert page.locator('.figure-editor').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
            assert page.locator('.figure-selection-actions > span').bounding_box()['height'] <= 60
            assert page.locator('.figure-main').evaluate('(el)=>{const stage=el.querySelector(".figure-stage").getBoundingClientRect(),main=el.getBoundingClientRect();return Math.min(stage.bottom,main.bottom)-Math.max(stage.top,main.top)>=350;}')
            page.screenshot(path=str(OUT/f'editor-en-{width}.png'), animations='disabled')
            if width == 768:
                page.locator('.figure-stage').scroll_into_view_if_needed()
                page.screenshot(path=str(OUT/'editor-en-768-canvas.png'), animations='disabled')
        checks.append('Styled editor controls remain visible in English without page-level horizontal overflow at desktop and narrow widths')

        page.set_viewport_size({'width':1440,'height':1000})
        letters=page.get_by_label('Label letter', exact=True)
        assert letters.locator('option').all_text_contents() == [chr(65+i) for i in range(26)]
        letters.select_option('Z')
        page.get_by_role('button', name='Add label', exact=True).click()
        label=page.get_by_label('Label content', exact=True)
        expect(label).to_have_value('Z')
        expect(label).to_be_focused()
        label.fill('Z. Results')
        expect(page.locator('.figure-canvas text').last).to_have_text('Z. Results')
        expect(page.get_by_role('button', name='Add A/B labels', exact=True)).to_have_count(0)
        page.screenshot(path=str(OUT/'independent-label-en.png'), animations='disabled')
        page.evaluate("async()=>{const m=await import('/src/i18n.ts');m.syncLanguage('zh')}")
        expect(page.get_by_label('标签字母', exact=True)).to_have_value('Z')
        expect(page.get_by_role('button', name='添加标签', exact=True)).to_be_visible()
        expect(page.get_by_label('标签内容', exact=True)).to_have_value('Z. Results')
        page.screenshot(path=str(OUT/'independent-label-zh.png'), animations='disabled')
        checks.append('Independent A–Z labels can be added without selecting a panel, edited immediately, and expose Chinese/English controls')
        assert not errors, errors
        assert not external, external
    except Exception:
        page.screenshot(path=str(OUT/'failure.png'), animations='disabled')
        (OUT/'failure.html').write_text(page.content(), encoding='utf-8')
        raise
    finally:
        (OUT/'result.json').write_text(json.dumps({'checks':checks,'pageErrors':errors,'externalRequests':external,'boundary':'Real styled renderer; file export dialog/IO mocked. No live model request or user data.'}, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()
print(json.dumps({'passed':len(checks),'errors':errors}, ensure_ascii=False))
