"""Bounded UI soak: repeated navigation, injected failures, timer cleanup and latency records."""
import json,os,statistics,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
OUT=ROOT/'validation-local/server-full-qa/browser';OUT.mkdir(parents=True,exist_ok=True)
names=['服务器文件','资源监控','环境管理','任务与调度','跨服务器数据','数据保护与审计','连接服务器']
errors=[];latencies=[];samples=[];rounds=0;faults=0;started=time.monotonic()
with sync_playwright() as p:
 edge=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
 browser=p.chromium.launch(executable_path=os.environ.get('OMAAX_QA_BROWSER') or (str(edge) if edge.is_file() else None),headless=True)
 page=browser.new_page(viewport={'width':1280,'height':800},locale='zh-CN');page.set_default_timeout(8000)
 page.add_init_script("""(()=>{const active=new Set(),set=window.setInterval.bind(window),clear=window.clearInterval.bind(window);window.setInterval=(...args)=>{const id=set(...args);active.add(id);return id;};window.clearInterval=id=>{active.delete(id);return clear(id);};Object.defineProperty(window,'__qaIntervals',{get:()=>active.size});})()""")
 page.on('pageerror',lambda error:errors.append(str(error)))
 try:
  page.goto('http://127.0.0.1:41739');page.wait_for_load_state('networkidle');expect(page.get_by_label('服务器 测试服务器 A',exact=True)).to_be_visible()
  nav=page.get_by_role('navigation',name='服务器管理栏目')
  for name in names:nav.get_by_role('button',name=name,exact=True).click()
  page.wait_for_timeout(100)
  cdp=page.context.new_cdp_session(page);cdp.send('HeapProfiler.collectGarbage')
  heap_before=cdp.send('Runtime.getHeapUsage');dom_before=cdp.send('Memory.getDOMCounters')
  baseline=page.evaluate('window.__qaIntervals');stop=time.monotonic()+90
  while time.monotonic()<stop:
   for name in names:
    begin=time.monotonic();nav.get_by_role('button',name=name,exact=True).click();expect(nav.get_by_role('button',name=name,exact=True)).to_have_attribute('aria-pressed','true');latencies.append((time.monotonic()-begin)*1000)
    assert page.locator('body').inner_text().strip()
   rounds+=1
   if rounds%5==0:
    page.evaluate("window.__qa.failures['metrics:qa-a']='Timed out while waiting for handshake'")
    nav.get_by_role('button',name='资源监控',exact=True).click();page.get_by_role('button',name='刷新',exact=True).click();expect(page.get_by_role('alert')).to_contain_text('SSH 握手超时')
    page.evaluate("delete window.__qa.failures['metrics:qa-a']");page.get_by_role('button',name='刷新',exact=True).click();expect(page.get_by_role('alert')).to_have_count(0);expect(page.get_by_label('服务器资源概览')).to_be_visible();faults+=1
    nav.get_by_role('button',name='连接服务器',exact=True).click()
   sample=page.evaluate('({intervals:window.__qaIntervals,heapBytes:performance.memory?.usedJSHeapSize||null})');samples.append(sample)
   assert sample['intervals']<=baseline+1,'Timers accumulated after page changes'
   page.evaluate('window.__qa.calls.splice(0,Math.max(0,window.__qa.calls.length-100))')
   if rounds%10==0:(OUT/'soak-progress.json').write_text(json.dumps({'rounds':rounds,'elapsedSeconds':round(time.monotonic()-started,1),'faultRecoveries':faults,'last':sample}),encoding='utf-8')
  assert not errors,errors;page.wait_for_timeout(100);cdp.send('HeapProfiler.collectGarbage');heap_after=cdp.send('Runtime.getHeapUsage');dom_after=cdp.send('Memory.getDOMCounters');page.screenshot(path=str(OUT/'soak-final.png'),full_page=True)
  report={'passed':True,'durationSeconds':round(time.monotonic()-started,1),'navigationCount':len(latencies),'rounds':rounds,'faultRecoveries':faults,'baselineIntervals':baseline,'finalIntervals':samples[-1]['intervals'],'peakSampledIntervals':max(s['intervals'] for s in samples),'latencyP50Ms':round(statistics.median(latencies),2),'latencyP95Ms':round(sorted(latencies)[int(len(latencies)*.95)-1],2),'heapBytesFirst':samples[0]['heapBytes'],'heapBytesLast':samples[-1]['heapBytes'],'pageErrors':errors,'scope':'real headless browser and production React components; synthetic services; 90-second bounded navigation soak, not a long-term leak proof'}
  report.update(heapAfterGcBefore=heap_before,heapAfterGcAfter=heap_after,domBefore=dom_before,domAfter=dom_after)
  (OUT/'soak-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
 finally:browser.close()
