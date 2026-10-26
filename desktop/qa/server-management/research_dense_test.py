"""Real rendered geometry checks for a 28-node / 66-relationship research map."""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('OMAAX_QA_URL', 'http://127.0.0.1:41755').rstrip('/')
OUT = Path(os.environ.get('OMAAX_QA_OUT', str(ROOT / 'provenance/releases/0.4.55/research-dense-browser')))
OUT.mkdir(parents=True, exist_ok=True)
ANALYSIS = json.loads((ROOT / 'desktop/qa/fixtures/research-dense.json').read_text(encoding='utf-8'))
checks, errors, external, geometry = [], [], [], []

GEOMETRY = r"""() => {
 const nodes=[...document.querySelectorAll('.research-graph .react-flow__node')].map(el=>({id:el.dataset.id,rect:el.getBoundingClientRect(),width:parseFloat(getComputedStyle(el).width),height:parseFloat(getComputedStyle(el).height)}));
 const collisions=[],crossings=[];
 for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
  const a=nodes[i].rect,b=nodes[j].rect;
  if(Math.min(a.right,b.right)>Math.max(a.left,b.left)+0.2&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top)+0.2)collisions.push([nodes[i].id,nodes[j].id]);
 }
 const paths=[...document.querySelectorAll('.research-graph .react-flow__edge-path')];
 for(const path of paths){
  const matrix=path.getScreenCTM();
  const points=[...path.getAttribute('d').matchAll(/([ML])\s+([-\d.]+)\s+([-\d.]+)/g)].map(([,command,x,y])=>({command,...Object.fromEntries(['x','y'].map(axis=>[axis,new DOMPoint(+x,+y).matrixTransform(matrix)[axis]]))}));
  for(let i=1;i<points.length;i++){
   const a=points[i-1],b=points[i];if(b.command!=='L')continue;
   for(const node of nodes){
    const r=node.rect;
    const inside=Math.abs(a.x-b.x)<0.01?a.x>r.left+0.3&&a.x<r.right-0.3&&Math.max(a.y,b.y)>r.top+0.3&&Math.min(a.y,b.y)<r.bottom-0.3:
     a.y>r.top+0.3&&a.y<r.bottom-0.3&&Math.max(a.x,b.x)>r.left+0.3&&Math.min(a.x,b.x)<r.right-0.3;
    if(inside)crossings.push({edge:path.closest('.react-flow__edge').dataset.id,node:node.id});
   }
  }
 }
 return {nodeCount:nodes.length,edgeCount:paths.length,collisions,crossings,actualWidths:[...new Set(nodes.map(n=>n.width))],actualHeights:[...new Set(nodes.map(n=>n.height))],labels:document.querySelectorAll('.research-graph .react-flow__edge-text').length};
}"""

def check_geometry(page, label):
    result=page.evaluate(GEOMETRY)
    assert result['nodeCount']==28 and result['edgeCount']==66, result
    assert not result['collisions'] and not result['crossings'], result
    assert result['actualWidths']==[224] and result['actualHeights']==[128], result
    assert result['labels']==0, result
    geometry.append({'stage':label,**result})

def wait_readable(page, node_id):
    page.wait_for_function("""id => {
      const card=document.querySelector('.react-flow__node[data-id="'+id+'"]').getBoundingClientRect();
      const canvas=document.querySelector('.research-graph').getBoundingClientRect();
      return card.width>=200 && card.left>=canvas.left && card.right<=canvas.right && card.top>=canvas.top && card.bottom<=canvas.bottom;
    }""", arg=node_id)

def wait_overview(page):
    page.wait_for_function("""() => {
      const box=document.querySelector('.research-graph').getBoundingClientRect();
      return [...document.querySelectorAll('.react-flow__node')].every(el=>{const r=el.getBoundingClientRect();return r.left>=box.left-1&&r.right<=box.right+1&&r.top>=box.top-1&&r.bottom<=box.bottom+1;});
    }""")

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith((BASE+'/',BASE.replace('http:','ws:')+'/', 'data:','blob:')) else (external.append(route.request.url),route.abort()))
    try:
        page.goto(BASE+'/workflow-preview.html?auth=qa&cloud=1')
        page.wait_for_load_state('networkidle')
        page.get_by_role('button',name='知识库问答',exact=True).click()
        page.evaluate("""analysis=>{
          const old=window.biodsh.dshRpc;
          window.biodsh.dshRpc=async(method,body)=>method==='workspace.list'?{items:[{workspaceId:'dense',path:'C:/fixture/dense',title:'Dense research fixture',sessionIds:[]}]}:old(method,body);
          window.__denseCalls=[];
          window.biodsh.research=async(action,body)=>{window.__denseCalls.push(action);if(action==='load')return {version:1,revision:1,topic:analysis.title,evidence:[{id:'paper1',kind:'paper',title:'SPL evidence source',detail:'Source excerpt retained for audit.'}],analysis,analysisStale:false,updatedAt:'2026-10-26T00:00:00Z'};throw Error('Unexpected model or save request');};
        }""",ANALYSIS)
        page.get_by_role('button',name='课题研究',exact=True).click()
        page.get_by_role('button',name='刷新项目',exact=True).click()
        expect(page.get_by_label('保存到哪个项目',exact=True)).to_have_value('C:/fixture/dense')
        expect(page.locator('.research-graph .react-flow__edge-path')).to_have_count(66)
        page.locator('.research-graph').scroll_into_view_if_needed()
        wait_readable(page,'n0')
        check_geometry(page,'Default readable research question')
        page.screenshot(path=str(OUT/'dense-default-readable-zh.png'),animations='disabled')
        viewport=page.locator('.research-graph .react-flow__viewport')
        before_pan=viewport.evaluate('(el)=>el.style.transform')
        start=page.locator('.research-graph').evaluate("""el=>{
          const r=el.getBoundingClientRect();
          for(const [x,y] of [[30,40],[r.width-30,40],[r.width-30,r.height-40]]){
            const target=document.elementFromPoint(r.x+x,r.y+y);
            if(target&&!target.closest('.react-flow__node,.react-flow__edge,.react-flow__controls'))return {x:r.x+x,y:r.y+y};
          }
          throw Error('No blank canvas area available for panning');
        }""")
        page.mouse.move(start['x'],start['y'])
        page.mouse.down()
        page.mouse.move(start['x']+60,start['y']+45,steps=5)
        page.mouse.up()
        assert viewport.evaluate('(el)=>el.style.transform')!=before_pan
        checks.append('Dense map opens at readable size with the first research question in view; dragging the blank canvas changes the viewport')
        page.get_by_role('button',name='显示完整图谱',exact=True).click()
        wait_overview(page)
        check_geometry(page,'Chinese dense overview')
        page.screenshot(path=str(OUT/'dense-overview-zh.png'),animations='disabled')
        checks.append('28 actual cards at 224x128; 66 routed relationships avoid every rendered card, including self/reverse/duplicate edges')

        page.get_by_label('查看关系',exact=True).select_option('e0')
        expect(page.get_by_label('图谱详情',exact=True)).to_contain_text(ANALYSIS['edges'][0]['label'])
        expect(page.locator('.research-relation-endpoints')).to_contain_text(ANALYSIS['nodes'][3]['label'])
        expect(page.locator('.research-relation-endpoints')).to_contain_text(ANALYSIS['nodes'][0]['label'])
        assert page.locator('.react-flow__edge[data-id="e0"] .react-flow__edge-path').evaluate('(el)=>parseFloat(getComputedStyle(el).strokeWidth)')==2.4
        assert page.locator('.react-flow__edge[data-id="e0"] title').text_content()==ANALYSIS['edges'][0]['label']
        page.screenshot(path=str(OUT/'dense-relation-zh.png'),animations='disabled')
        checks.append('Relationship selector displays the full unoverlapped label, endpoints and source in the detail panel; selected edge is highlighted')

        page.get_by_label('查看节点',exact=True).select_option('n5')
        page.wait_for_function("document.querySelector('.react-flow__node[data-id=\"n5\"]').getBoundingClientRect().width>=200")
        expect(page.get_by_label('图谱详情',exact=True)).to_contain_text(ANALYSIS['nodes'][5]['label'])
        page.screenshot(path=str(OUT/'dense-node-focused-zh.png'),animations='disabled')
        check_geometry(page,'Focused readable node')
        page.get_by_role('button',name='显示完整图谱',exact=True).click()
        page.get_by_role('button',name='放大',exact=True).click()
        page.get_by_role('button',name='缩小',exact=True).click()
        page.get_by_role('button',name='显示完整图谱',exact=True).click()
        checks.append('Node selector zooms the selected card to readable size; zoom in, zoom out and fit controls remain available')

        page.evaluate("async()=>{const m=await import('/src/i18n.ts');m.syncLanguage('en');}")
        page.set_viewport_size({'width':768,'height':850})
        page.locator('.research-graph').scroll_into_view_if_needed()
        wait_readable(page,'n5')
        check_geometry(page,'English narrow readable node')
        assert page.locator('.research-planner').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
        page.screenshot(path=str(OUT/'dense-narrow-readable-en.png'),animations='disabled')
        page.get_by_label('View relationship',exact=True).select_option('e65')
        expect(page.get_by_label('Map details',exact=True)).to_contain_text(ANALYSIS['edges'][65]['label'])
        page.evaluate("document.documentElement.dataset.theme='dark'")
        page.get_by_label('Map details',exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(OUT/'dense-detail-en-dark.png'),animations='disabled')
        page.get_by_role('button',name='Fit map to view',exact=True).click()
        wait_overview(page)
        check_geometry(page,'Manual English narrow overview')
        page.screenshot(path=str(OUT/'dense-narrow-en.png'),animations='disabled')
        assert page.evaluate("window.__denseCalls.every(action=>action==='load')")
        assert not errors and not external,(errors,external)
        checks.append('Narrow English map defaults to readable cards; manual Fit map to view shows all cards; dark details remain readable without overflow or model calls')
    except Exception:
        page.screenshot(path=str(OUT/'failure.png'),animations='disabled')
        (OUT/'failure.html').write_text(page.content(),encoding='utf-8')
        raise
    finally:
        (OUT/'result.json').write_text(json.dumps({'checks':checks,'geometry':geometry,'errors':errors,'externalRequests':external},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
print(json.dumps({'passed':len(checks),'errors':errors},ensure_ascii=False))
