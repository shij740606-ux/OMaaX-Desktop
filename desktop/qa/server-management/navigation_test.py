"""Navigation acceptance against the real UI with isolated synthetic data."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
out=ROOT/'validation-local/navigation-browser';out.mkdir(exist_ok=True)
checks=[];errors=[]
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
 page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('console',lambda m: print('CONSOLE',m.text,flush=True) if m.type=='error' else None)
 page.on('requestfailed',lambda r: print('REQUEST',r.url,r.failure,flush=True))
 try:
  page.goto('http://127.0.0.1:41742/workflow-preview.html?model=1&auth=local&eduUrl=http%3A%2F%2F127.0.0.1%3A41743%2F')
  page.wait_for_load_state('networkidle')

  if not page.evaluate('!!window.biodsh'):
   page.reload();page.wait_for_load_state('networkidle')
  page.wait_for_function('!!window.biodsh')
  page.evaluate("""()=>{
   const api=window.biodsh,old=api.personalCompute;window.__navCalls=[];
   api.workspaceFiles=async()=>[{name:'samples.csv',rel:'samples.csv',size:200,modified:1700000000,kind:'table'},{name:'result.tsv',rel:'analysis/result.tsv',size:100,modified:1700000000,kind:'table'}];
   api.personalCompute=async(id,action,body={})=>{
    window.__navCalls.push({id,action,body});
    if(action==='status')return {sources:{},images:[],protection:{},error:''};
    if(action==='jobs')return [{id:'job-result',state:'complete',created:1700000000,spec:{title:'服务器差异分析'},detail:''}];
    if(action==='workspace_files'){
     if(body.operation==='info')return {workspaceRoot:'/test',path:'/test'};
     if(body.operation==='contexts')return {items:[]};
     if(body.operation==='preview')return {text:'gene\\tvalue\\nGENE1\\t1'};
     return {files:[{name:'server-result.tsv',path:'/test/results/server-result.tsv',directory:false,size:123}],nextOffset:null};
    }
    return old(id,action,body);
   };
  }""")
  page.get_by_role('button',name='分析',exact=True).click()
  expect(page.get_by_text('samples.csv',exact=True)).to_be_visible()
  expect(page.get_by_text('分析产出',exact=False)).to_be_visible()
  expect(page.get_by_role('heading',name='长流程分析结果')).to_have_count(0)
  page.screenshot(path=str(out/'analysis.png'));checks.append('original workspace cards and recommended actions are the default')
  page.get_by_role('button',name='长流程结果',exact=True).click()
  expect(page.get_by_role('heading',name='长流程分析结果')).to_be_visible()
  page.get_by_role('button',name='服务器结果',exact=True).click()
  expect(page.get_by_text('服务器差异分析',exact=False)).to_be_visible()
  expect(page.get_by_text('server-result.tsv',exact=True)).to_be_visible()
  page.get_by_role('button',name='下载结果',exact=True).click()
  expect(page.get_by_role('status')).to_contain_text('结果已下载到')
  page.get_by_role('button',name='移入回收站',exact=True).click()
  expect(page.get_by_role('alertdialog')).to_be_visible()
  page.get_by_role('button',name='取消',exact=True).click()
  assert not page.evaluate("window.__navCalls.some(c=>c.body.operation==='delete')")
  checks.append('server results download and file listing; cancelled deletion makes no delete request')
  page.screenshot(path=str(out/'server-results.png'))
  page.get_by_role('button',name='工作区分析',exact=True).click()
  expect(page.get_by_text('samples.csv',exact=True)).to_be_visible()
  expect(page.get_by_role('button',name='账户与用量',exact=True)).to_have_count(0)
  expect(page.get_by_role('button',name='点击查看用量明细',exact=True)).to_have_count(0)
  page.get_by_role('button',name='设置',exact=True).click()
  page.get_by_role('tab',name='账号和用量',exact=False).click()
  expect(page.get_by_role('heading',name='模型使用情况')).to_be_visible()
  expect(page.get_by_text('本月用量',exact=True)).to_be_visible()
  checks.append('account and actual model usage accessible only inside settings')
  page.screenshot(path=str(out/'settings-account.png'))
  for width in [1440,1000,768]:
   page.set_viewport_size({'width':width,'height':1000})
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  page.set_viewport_size({'width':1440,'height':1000})
  page.get_by_role('button',name='教育科研',exact=True).click()
  frame=page.frame_locator('iframe[title="教育科研工作台"]')
  expect(frame.locator('.edu-item')).to_have_count(9)
  expect(frame.get_by_role('button',name='返回问答',exact=False)).to_have_count(0)
  frame.get_by_role('button',name='科研汇报助手',exact=True).click()
  expect(frame.locator('[data-test="generate"]')).to_be_visible()
  page.screenshot(path=str(out/'education.png'));checks.append('nine research modules remain, return-to-chat entry removed')
  assert not errors,errors
 finally:
  (out/'result.json').write_text(json.dumps({'checks':checks,'errors':errors,'mode':'synthetic bridge; real React/Vue and browser'},ensure_ascii=False,indent=2),encoding='utf-8')
  browser.close()
print(json.dumps({'passed':len(checks),'errors':errors}))
