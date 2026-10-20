"""Exercise the bundled chat presentation script without a model or server."""
import sys,re,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
source=(ROOT/'desktop/src-tauri/src/lib.rs').read_text(encoding='utf-8')
script=re.search(r'const DSH_INIT_SCRIPT: &str = r#"(.*?)"#;',source,re.S).group(1)
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
 page=b.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content("""<div class='_frame' style='display:grid;grid-template-columns:200px 1fr'><aside></aside><div class='_column'>
 <div data-chat-flow-kind='user' data-chat-flow-key='a'>用户在服务器管理中选择了以下 SSH 服务器：测试（编号 secret-id）。请使用 mcp__biodsh_ssh__list_servers 确认当前选择。<button>复制</button></div>
 <div data-chat-flow-kind='context'>INTERNAL_ROUTING_SECRET</div>
 <div data-chat-flow-kind='tool-call'><span class='_title'>mcp__biodsh_ssh__get_resources</span><span>连接失败，请检查服务器网络</span></div>
 <div data-chat-flow-kind='assistant-step'><div class='_markdown_test'>服务器连接失败，请检查网络后重试。</div></div>
 <div data-chat-flow-kind='turn-tail'></div>
 </div></div>""")
 page.evaluate(script)
 expect(page.get_by_text('已选择服务器，请查看可用资源并协助后续分析。',exact=True)).to_be_visible()
 assert 'secret-id' not in page.locator('body').inner_text()
 expect(page.get_by_role('button',name='复制')).not_to_be_visible()
 expect(page.get_by_text('服务器连接失败，请检查网络后重试。',exact=True)).to_be_visible()
 page.locator('.biodsh-sum').click()
 assert 'INTERNAL_ROUTING_SECRET' not in page.locator('body').inner_text()
 expect(page.get_by_text('连接失败，请检查服务器网络',exact=True)).to_be_visible()
 page.evaluate("document.querySelector('[data-chat-flow-kind=user]').remove()")
 expect(page.locator('.omaax-handoff')).to_have_count(0)
 assert not errors,errors
 b.close()
print(json.dumps({'passed':4,'errors':errors}))
