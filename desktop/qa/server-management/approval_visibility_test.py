"""Real stable DSH approvals with an isolated local model and synthetic tool.

The shell case is always rejected before execution. Generic approval executes
only this fixture's in-memory return value. No company login or real model API.
"""
import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--mode', choices=['before', 'after'], required=True)
parser.add_argument('--cases', choices=['core', 'extra'], default='core')
args = parser.parse_args()
run_name=args.mode if args.cases=='core' else args.mode+'-extra'
OUT = ROOT/'provenance/pending/approval-visibility-057'
OUT.mkdir(parents=True, exist_ok=True)
source = OUT/'before-lib.rs' if args.mode == 'before' else ROOT/'desktop/src-tauri/src/lib.rs'
init = re.search(r'const DSH_INIT_SCRIPT: &str = r#"([\s\S]*?)"#;', source.read_text(encoding='utf8')).group(1)
runtime = ROOT/'desktop/dsh-runtime/node_modules/@deepseek-ai/dsh'
node = ROOT/'desktop/resources/node/win32-x64/node.exe'
report = {'mode':args.mode, 'runtimeVersion':json.loads((runtime/'package.json').read_text())['version'],
          'initSha256':hashlib.sha256(init.encode()).hexdigest(), 'scenarios':[], 'pageErrors':[], 'externalRequests':[],
          'boundary':'Real bundled DSH backend/frontend and unchanged native initialization script; local synthetic model, isolated profile. Native window hide/show approximated by preserving document and changing visibility/size. Shell approval always rejected; no user command or cloud access.'}
counts = {}
class Model(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        match = re.search(r'CASE:(\w+)', json.dumps(body.get('messages', [])))
        if not body.get('stream'):
            result = json.dumps({'choices':[{'message':{'role':'assistant','content':'Approval fixture'},'finish_reason':'stop'}]}).encode()
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(result)
            return
        mode = match.group(1) if match else ''
        if not mode or not body.get('tools'):
            delta, finish = {'content':'Approval fixture'}, 'stop'
        else:
            counts[mode] = counts.get(mode, 0)+1
            if counts[mode] == 1:
                shell = 'shell' in mode or 'nested' in mode
                payload = {'command':"Write-Output 'SYNTHETIC_COMMAND_NEVER_EXECUTED'",'description':'Preview the synthetic approval fixture command'} if shell else {'payload':mode}
                name='pwsh' if shell else 'qa_approval_probe'
                if 'nested' in mode:
                    name='run_code'; payload={'code':'return await tools.pwsh('+json.dumps(payload)+');','description':'Request approval for a nested synthetic command'}
                if 'missing' in mode: name='qa_missing_history'
                delta = {'tool_calls':[{'index':0,'id':'fixture_'+mode,'type':'function','function':{'name':name,'arguments':json.dumps(payload)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'content':'Fixture finished after decision.'}, 'stop'
        result = ('data: '+json.dumps({'id':'fixture','choices':[{'index':0,'delta':delta,'finish_reason':None}]})+'\n\n'+
                  'data: '+json.dumps({'choices':[{'index':0,'delta':{},'finish_reason':finish}],'usage':{'prompt_tokens':20,'completion_tokens':4,'total_tokens':24}})+'\n\ndata: [DONE]\n\n').encode()
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Content-Length',str(len(result))); self.end_headers(); self.wfile.write(result)

model = ThreadingHTTPServer(('127.0.0.1',0),Model)
threading.Thread(target=model.serve_forever,daemon=True).start()
processes=[]
def start_dsh(lang):
    home=OUT/f'isolated-{args.mode}-{lang}-{time.time_ns()}'
    workspace=home/'workspace'; workspace.mkdir(parents=True)
    (home/'settings.yaml').write_text(f'ui-onboarding:\n  welcomeNoticeVersion: 2026-08-13.1\nlocale:\n  preference: {lang}\nui-theme:\n  preference: light\n',encoding='utf8')
    event_file=home/'events.jsonl'
    plugin=home/'fixture.mjs'
    plugin.write_text('''import {defineContentToolFixture} from TOOLS;
import {appendFileSync} from 'node:fs';
export const inject=['tools','approval'];
export function apply(ctx){
 ctx.on('session/event',(session,event)=>appendFileSync(EVENTS,JSON.stringify({sessionId:session.id,event})+'\\n'),{global:true});
 ctx.tools.register(defineContentToolFixture({name:'qa_approval_probe',description:'Synthetic approval fixture only',parameters:{payload:{type:'string',required:true}},execute:async()=>[{type:'text',text:'SYNTHETIC_TOOL_COMPLETED'}]}));
 ctx.tools.register(defineContentToolFixture({name:'qa_missing_history',description:'Synthetic approval with deliberately absent correlated history',parameters:{payload:{type:'string',required:true}},execute:async(args,exec)=>{
  const outcome=await ctx.approval.request({agent:exec.agent,toolName:'qa_missing_history',callId:exec.callId+':deliberately-absent',reason:'Fixture: confirm an operation whose detail has not arrived.',signal:exec.signal});
  return [{type:'text',text:'Synthetic missing-history decision: '+outcome}];
 }}));
 ctx.on('tools/pre-execute',async(exec)=>{
  if(exec.name==='qa_approval_probe')return {kind:'ask',...String(exec.arguments?.payload).includes('fallback')?{}:{reason:String(exec.arguments?.payload).includes('blank')?'   ':'Fixture: confirm exporting the synthetic summary.'}};
  if(exec.name==='pwsh')return {kind:'ask',reason:'Fixture: confirm previewing the synthetic command.'};
  if(exec.name==='run_code'||exec.name==='qa_missing_history')return {kind:'allow'};
  return {kind:'deny',reason:'All other tools are disabled in this isolated fixture'};
 });
}
'''.replace('TOOLS',json.dumps((runtime/'node_modules/@deepseek-ai/dsh-tools/lib/index.js').as_uri())).replace('EVENTS',json.dumps(str(event_file))),encoding='utf8')
    dsh_source=(ROOT/'desktop/src-tauri/src/dsh.rs').read_text(encoding='utf8')
    disabled=re.findall(r'"([^"]+)"',re.search(r'const MINIMAL_PLUGINS: &\[&str\] = &\[([\s\S]*?)\];',dsh_source).group(1))
    patch=''.join(f'- id: {name}\n  disabled: true\n' for name in disabled)
    patch+=f'- id: llm-deepseek\n  config:\n    baseURL: http://127.0.0.1:{model.server_port}/v1\n    apiKeyEnv: OMAAX_APPROVAL_FIXTURE_KEY\n- insert:\n    - id: approval-fixture\n      name: {json.dumps(plugin.as_uri())}\n'
    if args.cases=='extra': patch+='- id: tools\n  config:\n    mode: both\n'
    (home/'cordis.patch.yml').write_text(patch,encoding='utf8')
    env={k:v for k,v in os.environ.items() if not re.search(r'(key|token|password|secret|credential|proxy)',k,re.I) and not k.startswith(('DSH_','OMAAX_','BIODSH_')) and k!='NODE_OPTIONS'}
    env.update(DSH_HOME=str(home),DSH_TELEMETRY_DISABLED='1',DSH_BUNDLED_SKILL_DIR=str(home/'no-skills'),OMAAX_APPROVAL_FIXTURE_KEY='synthetic-only')
    proc=subprocess.Popen([str(node),'--expose-internals',str(runtime/'lib/bin.js'),'web','--port','0','--no-open'],cwd=workspace,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
    processes.append(proc)
    lines=queue.Queue()
    def read():
        for line in proc.stdout: lines.put(line)
    threading.Thread(target=read,daemon=True).start()
    log=[]; deadline=time.monotonic()+55
    while time.monotonic()<deadline:
        try: line=lines.get(timeout=.2)
        except queue.Empty:
            if proc.poll() is not None: raise RuntimeError('DSH exited: '+''.join(log)[-2500:])
            continue
        log.append(line)
        match=re.search(r'http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9._-]+',''.join(log))
        if match: return proc,match.group(),home,event_file
    raise RuntimeError('DSH startup timed out: '+''.join(log)[-2500:])

try:
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True,args=['--no-proxy-server'])
        for lang in ['zh','en']:
            proc,url,home,event_file=start_dsh(lang)
            origin=f'{urlparse(url).scheme}://{urlparse(url).netloc}'
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN' if lang=='zh' else 'en-US')
            def route(request):
                if urlparse(request.request.url).hostname not in ('127.0.0.1','localhost',None):
                    report['externalRequests'].append(request.request.url); request.abort()
                else: request.continue_()
            context.route('**/*',route)
            context.add_init_script(init)
            context.request.get(url)
            def rpc(endpoint,payload):
                response=context.request.post(origin+'/api/'+endpoint,data={'type':'client-request','rpcId':str(uuid.uuid4()),'method':endpoint,'payload':{'args':{'request':payload}}}).json()['result']
                assert response['ok'],response
                return response['value']
            ws=rpc('workspace/create',{'path':str(home/'workspace')}); wid=ws.get('workspaceId') or ws['workspace']['workspaceId']
            page=context.new_page(); page.on('pageerror',lambda err:report['pageErrors'].append(str(err)))
            page.goto(url); page.wait_for_load_state('networkidle')
            welcome=page.locator('[class*="_headline"]').first
            assert welcome.count()==0 or not welcome.is_visible(), 'Original welcome brand returned'
            page.screenshot(path=str(OUT/f'{run_name}-{lang}-welcome.png'))
            if args.mode=='after':
                ordinary=page.evaluate('''()=>{const p=document.createElement('div');p.className='fixture_headline';p.textContent='Unrelated context headline';document.body.append(p);const result=getComputedStyle(p).display;p.remove();return result;}''')
                assert ordinary!='none','Unrelated headline still hidden'
            for scenario in (['generic','shell','fallback'] if args.cases=='core' else ['nested','missing','blank']):
                created=rpc('session/create',{'workspaceId':wid}); sid=created.get('sessionId') or created.get('session',{}).get('sessionId') or created['id']
                page.evaluate('(sid)=>localStorage.setItem("dsh.sessions.current",JSON.stringify({sessionId:sid}))',sid)
                page.reload(); page.wait_for_load_state('networkidle')
                expect(page.locator('[data-composer-input]')).to_be_visible()
                mode=f'{lang}_{scenario}'
                rpc('session/prompt',{'sessionId':sid,'mode':'queue','requestId':str(uuid.uuid4()),'content':[{'type':'text','text':'CASE:'+mode+' Request the synthetic fixture approval.'}]})
                card=page.locator('[data-approval-key]'); expect(card).to_be_visible(timeout=20000)
                headline=card.locator('[class*="_headline"]')
                expected='Fixture: confirm previewing the synthetic command.' if scenario in ('shell','nested') else 'Fixture: confirm exporting the synthetic summary.'
                if scenario=='missing': expected='Fixture: confirm an operation whose detail has not arrived.'
                if scenario in ('fallback','blank'): assert 'qa_approval_probe' in headline.text_content()
                else: expect(headline).to_have_text(expected)
                visible=headline.is_visible()
                assert visible==(args.mode=='after'), {'scenario':scenario,'visible':visible,'expectedMode':args.mode}
                if scenario in ('shell','nested'): expect(card.get_by_text("Write-Output 'SYNTHETIC_COMMAND_NEVER_EXECUTED'",exact=False)).to_be_visible()
                if scenario=='missing':
                    unavailable='暂未读取到这次操作的详细参数，请先核对审批说明。' if lang=='zh' else 'Details for this operation are not available yet. Review the approval description before deciding.'
                    expect(card.get_by_text(unavailable,exact=True)).to_be_visible()
                key=card.get_attribute('data-approval-key')
                page.screenshot(path=str(OUT/f'{run_name}-{lang}-{scenario}.png'))
                # Settings hides and resizes the retained native view; it does not reload its document.
                page.evaluate('document.documentElement.style.visibility="hidden"')
                page.set_viewport_size({'width':1,'height':1})
                page.set_viewport_size({'width':1440,'height':1000})
                page.evaluate('document.documentElement.style.visibility=""')
                expect(card).to_be_visible()
                assert card.get_attribute('data-approval-key')==key
                assert headline.is_visible()==visible
                page.screenshot(path=str(OUT/f'{run_name}-{lang}-{scenario}-returned.png'))
                rejected=scenario in ('shell','nested','missing')
                choice=('拒绝' if lang=='zh' else 'Reject') if rejected else ('允许一次' if lang=='zh' else 'Allow once')
                card.get_by_role('button',name=choice,exact=True).click()
                expect(card).to_have_count(0)
                expect(page.get_by_text('Fixture finished after decision.',exact=True)).to_be_visible(timeout=15000)
                rows=[json.loads(line) for line in event_file.read_text(encoding='utf8').splitlines()]
                events=[row['event'] for row in rows if row['sessionId']==sid]
                decisions=[e for e in events if e['type']=='approval/decided']
                assert len(decisions)==1, decisions
                if rejected: assert 'rejected' in json.dumps(decisions)
                else: assert 'allowed-once' in json.dumps(decisions)
                if scenario=='nested':
                    subcalls=[e for e in events if e['type']=='tool/ptc-dispatch-start']
                    assert len(subcalls)==1 and subcalls[0]['data']['name']=='pwsh',subcalls
                page.reload(); page.wait_for_load_state('networkidle')
                expect(page.get_by_text('Fixture finished after decision.',exact=True)).to_be_visible()
                expect(page.locator('[data-approval-key]')).to_have_count(0)
                report['scenarios'].append({'language':lang,'case':scenario,'headlineVisible':visible,'samePendingAfterReturn':True,'decisionCount':1,'resolvedConversationRestoresAfterReload':True,'shellExecution':'rejected before execution' if scenario in ('shell','nested') else 'not a shell tool'})
            if args.cases=='extra':
                context.close(); proc.terminate(); proc.wait(timeout=10)
                continue
            # A native session switch explicitly reloads, unlike the Settings path.
            # Record whether DSH reissues or cancels the pending transport request;
            # either way, reloading must never grant an approval automatically.
            created=rpc('session/create',{'workspaceId':wid}); sid=created.get('sessionId') or created.get('session',{}).get('sessionId') or created['id']
            page.evaluate('(sid)=>localStorage.setItem("dsh.sessions.current",JSON.stringify({sessionId:sid}))',sid)
            page.reload(); page.wait_for_load_state('networkidle')
            expect(page.locator('[data-composer-input]')).to_be_visible()
            rpc('session/prompt',{'sessionId':sid,'mode':'queue','requestId':str(uuid.uuid4()),'content':[{'type':'text','text':'CASE:'+lang+'_pendingreload Request the synthetic fixture approval.'}]})
            expect(page.locator('[data-approval-key]')).to_be_visible(timeout=20000)
            page.reload(); page.wait_for_load_state('networkidle')
            pending=page.locator('[data-approval-key]')
            page.wait_for_function('()=>document.querySelector("[data-approval-key]")||document.body.innerText.includes("Fixture finished after decision.")',timeout=15000)
            restored=pending.count()>0
            if restored:
                assert pending.locator('[class*="_headline"]').is_visible()==(args.mode=='after')
                page.screenshot(path=str(OUT/f'{args.mode}-{lang}-pending-reloaded.png'))
                pending.get_by_role('button',name='拒绝' if lang=='zh' else 'Reject',exact=True).click()
            expect(page.get_by_text('Fixture finished after decision.',exact=True)).to_be_visible(timeout=15000)
            events=[json.loads(line)['event'] for line in event_file.read_text(encoding='utf8').splitlines() if json.loads(line)['sessionId']==sid]
            decisions=[event for event in events if event['type']=='approval/decided']
            assert decisions and 'allowed-once' not in json.dumps(decisions), decisions
            report['scenarios'].append({'language':lang,'case':'pending-session-reload-diagnostic','pendingReissued':restored,'decisions':decisions,'neverAutoApproved':True})
            context.close(); proc.terminate(); proc.wait(timeout=10)
        browser.close()
    assert not report['pageErrors'],report['pageErrors']
    assert not report['externalRequests'],report['externalRequests']
except Exception as error:
    report['failure']=str(error)
    try:
        page.screenshot(path=str(OUT/f'{run_name}-failure.png'))
        (OUT/f'{run_name}-failure.html').write_text(page.content(),encoding='utf8')
    except Exception: pass
    raise
finally:
    (OUT/f'{run_name}-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    for proc in processes:
        if proc.poll() is None:
            proc.terminate(); proc.wait(timeout=10)
    model.shutdown(); model.server_close()
print(json.dumps({'mode':args.mode,'passed':len(report['scenarios']),'pageErrors':report['pageErrors']}))
