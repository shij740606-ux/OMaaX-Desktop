"""Real browser regression for graph replacements, workers and request-scoped progress."""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
BASE=os.environ.get('OMAAX_QA_URL','http://127.0.0.1:41755').rstrip('/')
OUT=ROOT/'provenance/pending/research-reliability-056/browser'
OUT.mkdir(parents=True,exist_ok=True)
ANALYSIS=json.loads((ROOT/'desktop/qa/fixtures/research-dense.json').read_text(encoding='utf-8'))
checks,errors,external=[],[],[]
timing={}

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith((BASE+'/',BASE.replace('http:','ws:')+'/','data:','blob:')) else (external.append(route.request.url),route.abort()))
    try:
        page.goto(BASE+'/workflow-preview.html?auth=qa&cloud=1')
        page.wait_for_load_state('networkidle')
        page.evaluate('''()=>{
          const NativeWorker=window.Worker;window.__layoutWorkers=[];window.__layoutMode='normal';
          window.Worker=class extends NativeWorker{
            constructor(...args){super(...(window.__layoutMode==='fail'?[new URL('/missing-research-worker.js',location.href),args[1]]:args));this.testMode=window.__layoutMode;this.terminated=false;this.held=[];window.__layoutWorkers.push(this);}
            postMessage(value){this.input=value;if(this.testMode==='hold'){this.held.push(value);return;}super.postMessage(value);}
            release(){if(!this.terminated)this.held.splice(0).forEach(value=>super.postMessage(value));}
            terminate(){this.terminated=true;super.terminate();}
          };
        }''')
        timing=page.evaluate('''async analysis=>{
          const longTasks=[];let last=performance.now(),maxTickGap=0;
          const observer=new PerformanceObserver(list=>list.getEntries().forEach(entry=>longTasks.push(entry.duration)));
          observer.observe({entryTypes:['longtask']});
          const timer=setInterval(()=>{const now=performance.now();maxTickGap=Math.max(maxTickGap,now-last);last=now;},10);
          const start=performance.now();const m=await import('/src/views/research-graph-layout.ts');await m.layoutResearchGraph(analysis);
          const elapsed=performance.now()-start;await new Promise(resolve=>setTimeout(resolve,100));
          clearInterval(timer);observer.disconnect();return {elapsed,maxTickGap,longTasks,workers:window.__layoutWorkers.length,allTerminated:window.__layoutWorkers.every(w=>w.terminated)};
        }''',ANALYSIS)
        assert timing['workers']>=1 and timing['allTerminated'],timing
        assert timing['maxTickGap']<150,timing
        checks.append('Dense layout runs in an actual worker, terminates on completion, and leaves the UI heartbeat responsive')
        page.get_by_role('button',name='知识库问答',exact=True).click()
        page.evaluate('''analysis=>{
          window.__research={listeners:[],pending:[],calls:[],saved:{}};
          window.biodsh.onEvent=fn=>{window.__research.listeners.push(fn);return()=>{window.__research.listeners=window.__research.listeners.filter(item=>item!==fn);};};
          window.__emitResearch=event=>window.__research.listeners.forEach(fn=>fn(event));
          window.__nextAnalysis=prefix=>({...analysis,title:prefix+' study',nodes:analysis.nodes.map(node=>({...node,id:prefix+'-'+node.id})),edges:analysis.edges.map(edge=>({...edge,id:prefix+'-'+edge.id,source:prefix+'-'+edge.source,target:prefix+'-'+edge.target}))});
          const old=window.biodsh.dshRpc;
          window.biodsh.dshRpc=async(method,body)=>method==='workspace.list'?{items:['A','B'].map(id=>({workspaceId:id,path:'C:/fixture/'+id,title:'Research '+id,sessionIds:[]}))}:old(method,body);
          window.biodsh.research=async(action,body)=>{
            window.__research.calls.push({action,body});
            if(action==='load')return structuredClone(window.__research.saved[body.project]||{version:1,revision:1,topic:body.project,evidence:[],analysis:body.project.endsWith('A')?analysis:window.__nextAnalysis('project-B'),analysisStale:false,updatedAt:'2026-10-26'});
            if(action==='generate')return new Promise((resolve,reject)=>window.__research.pending.push({resolve,reject,requestId:body.requestId}));
            if(action==='save'){const value={...body.state,revision:body.expectedRevision+1,updatedAt:new Date().toISOString()};window.__research.saved[body.project]=value;return value;}
            return {};
          };
        }''',ANALYSIS)
        page.get_by_role('button',name='课题研究',exact=True).click()
        page.get_by_role('button',name='刷新项目',exact=True).click()
        expect(page.get_by_label('保存到哪个项目',exact=True)).to_have_value('C:/fixture/A')
        expect(page.locator('.research-graph .react-flow__node')).to_have_count(28)

        page.get_by_role('button',name='更新图谱与建议',exact=True).click()
        expect(page.locator('.research-progress')).to_contain_text('正在连接模型服务')
        page.wait_for_function("document.querySelector('.research-progress').textContent.includes('已等待 1 秒')")
        page.evaluate("window.__emitResearch({type:'research-progress',requestId:window.__research.pending[0].requestId,phase:'generating',receivedChars:1234})")
        expect(page.locator('.research-progress')).to_contain_text('1,234')
        page.evaluate("window.__emitResearch({type:'research-progress',requestId:'wrong-request',phase:'validating',receivedChars:9999})")
        expect(page.locator('.research-progress')).not_to_contain_text('9,999')
        page.screenshot(path=str(OUT/'receiving-progress.png'),animations='disabled')
        page.evaluate("window.__emitResearch({type:'research-progress',requestId:window.__research.pending[0].requestId,phase:'validating',receivedChars:2345})")
        expect(page.locator('.research-progress')).to_contain_text('正在校验研究内容')
        page.evaluate("window.__layoutMode='hold';window.__research.pending[0].resolve(window.__nextAnalysis('updated'))")
        expect(page.locator('.research-graph')).to_contain_text('正在整理图谱布局')
        assert page.locator('.research-graph .react-flow__node').count()==0
        expect(page.locator('#research-topic')).to_have_value('C:/fixture/A')
        page.get_by_role('tab',name='下一步建议',exact=True).click()
        expect(page.locator('.research-suggestions')).to_be_visible()
        page.get_by_role('tab',name='关系图谱',exact=True).click()
        page.evaluate("window.__layoutWorkers.filter(w=>!w.terminated).forEach(w=>w.release());window.__layoutMode='normal'")
        expect(page.locator('.react-flow__node[data-id="updated-n0"]')).to_have_count(1)
        page.screenshot(path=str(OUT/'different-ids-after-update.png'),animations='disabled')
        checks.append('Real update changes all 28 node IDs safely; progress shows current phase/count/time; slow layout keeps tabs and page usable')

        page.get_by_role('button',name='更新图谱与建议',exact=True).click()
        page.get_by_role('button',name='取消更新',exact=True).click()
        page.evaluate("window.__emitResearch({type:'research-progress',requestId:window.__research.pending[1].requestId,phase:'validating',receivedChars:9999});window.__research.pending[1].resolve(window.__nextAnalysis('cancelled'))")
        expect(page.locator('.research-progress')).to_have_count(0)
        expect(page.locator('.react-flow__node[data-id="updated-n0"]')).to_have_count(1)
        assert page.evaluate("window.__research.calls.filter(call=>call.action==='save').length")==1
        checks.append('Cancellation keeps the last saved map and ignores late progress/results without another save')

        page.get_by_role('button',name='更新图谱与建议',exact=True).click()
        page.evaluate("window.__layoutMode='hold';window.__research.pending[2].resolve(window.__nextAnalysis('obsolete'))")
        expect(page.locator('.research-graph')).to_contain_text('正在整理图谱布局')
        page.get_by_role('button',name='更新图谱与建议',exact=True).click()
        page.evaluate("window.__layoutMode='normal';window.__research.pending[3].resolve(window.__nextAnalysis('latest'))")
        expect(page.locator('.react-flow__node[data-id="latest-n0"]')).to_have_count(1)
        assert page.evaluate("(()=>{const workers=window.__layoutWorkers.filter(w=>w.input?.graph?.children[0]?.id==='node/obsolete-n0');return workers.length>0&&workers.every(w=>w.terminated);})()")
        checks.append('A second update aborts the older pending layout and publishes only the newest graph')

        page.get_by_role('button',name='更新图谱与建议',exact=True).click()
        page.evaluate("window.__layoutMode='hold';window.__research.pending[4].resolve(window.__nextAnalysis('leave-project'))")
        expect(page.locator('.research-graph')).to_contain_text('正在整理图谱布局')
        page.evaluate("window.__layoutMode='normal'")
        page.get_by_label('保存到哪个项目',exact=True).select_option('C:/fixture/B')
        expect(page.locator('.react-flow__node[data-id="project-B-n0"]')).to_have_count(1)
        assert page.evaluate("(()=>{const workers=window.__layoutWorkers.filter(w=>w.input?.graph?.children[0]?.id==='node/leave-project-n0');return workers.length>0&&workers.every(w=>w.terminated);})()")
        checks.append('Switching project terminates outstanding layout work and shows only the destination project')

        page.get_by_role('button',name='更新图谱与建议',exact=True).click()
        page.evaluate("window.__layoutMode='fail';window.__research.pending[5].resolve(window.__nextAnalysis('recoverable'))")
        expect(page.locator('.research-graph')).to_contain_text('图谱布局未能完成，研究内容已保留')
        expect(page.locator('#research-topic')).to_have_value('C:/fixture/B')
        page.get_by_role('tab',name='下一步建议',exact=True).click()
        expect(page.locator('.research-suggestions')).to_be_visible()
        page.get_by_role('tab',name='关系图谱',exact=True).click()
        page.screenshot(path=str(OUT/'worker-failure-contained.png'),animations='disabled')
        generations=page.evaluate("window.__research.calls.filter(call=>call.action==='generate').length")
        page.evaluate("window.__layoutMode='normal'")
        page.get_by_role('button',name='重新布局',exact=True).click()
        expect(page.locator('.react-flow__node[data-id="recoverable-n0"]')).to_have_count(1)
        assert page.evaluate("window.__research.calls.filter(call=>call.action==='generate').length")==generations
        checks.append('Worker failure is local to the graph; saved content and suggestions survive; retry recovers without a model request')
        assert not errors and not external,(errors,external)
        page.screenshot(path=str(OUT/'recovered.png'),animations='disabled')
    except Exception:
        page.screenshot(path=str(OUT/'failure.png'),animations='disabled')
        (OUT/'failure.html').write_text(page.content(),encoding='utf-8')
        raise
    finally:
        (OUT/'result.json').write_text(json.dumps({'checks':checks,'workerTiming':timing,'pageErrors':errors,'externalRequests':external},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
print(json.dumps({'passed':len(checks),'errors':errors},ensure_ascii=False))
