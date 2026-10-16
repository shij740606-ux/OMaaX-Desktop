"""Real browser checks of production React components with an isolated service fixture."""
import json,os,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation-local/qa-runtime/python'))
from playwright.sync_api import sync_playwright,expect
OUT=ROOT/'validation-local/server-full-qa/browser';OUT.mkdir(parents=True,exist_ok=True)
URL='http://127.0.0.1:41739'
results=[];errors=[]
def run(name,work):
 start=time.monotonic()
 try:work();results.append({'name':name,'passed':True,'seconds':round(time.monotonic()-start,3)});print('PASS '+name,flush=True)
 except Exception as error:
  page.screenshot(path=str(OUT/('failure-'+str(len(results))+'.png')),full_page=True)
  results.append({'name':name,'passed':False,'error':str(error),'traceback':traceback.format_exc()})
  print('FAIL '+name+': '+str(error),flush=True)
  close=page.get_by_role('button',name='确认结果并关闭',exact=True)
  if close.count():close.click()
def click(name):page.get_by_role('button',name=name,exact=True).click()
def tab(name):page.get_by_role('navigation',name='服务器管理栏目').get_by_role('button',name=name,exact=True).click()
def fresh():
 page.goto(URL);page.wait_for_load_state('networkidle');expect(page.get_by_label('服务器 测试服务器 A',exact=True)).to_be_visible()
def card(letter):return page.get_by_label('服务器 测试服务器 '+letter,exact=True)
def calls(name):return page.evaluate('(name)=>window.__qa.calls.filter(c=>c.name===name)',name)
def no_overflow():assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),'Horizontal page overflow'

with sync_playwright() as p:
 edge=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
 browser=p.chromium.launch(executable_path=os.environ.get('OMAAX_QA_BROWSER') or (str(edge) if edge.is_file() else None),headless=True)
 context=browser.new_context(viewport={'width':1600,'height':1000},locale='zh-CN',permissions=['clipboard-read','clipboard-write'])
 context.tracing.start(screenshots=True,snapshots=True,sources=False)
 page=context.new_page();page.set_default_timeout(8000);page.on('pageerror',lambda error:errors.append(str(error)))
 page.on('console',lambda msg:errors.append('console: '+msg.text) if msg.type=='error' else None)
 try:
  fresh()
  # Reconnaissance precedes interaction; save actual rendered roles and default layout.
  (OUT/'initial-dom.html').write_text(page.content(),encoding='utf-8')
  (OUT/'initial-controls.json').write_text(json.dumps(page.locator('button').all_text_contents(),ensure_ascii=False,indent=2),encoding='utf-8')
  page.screenshot(path=str(OUT/'connections.png'),full_page=True)
  if '--inspect' not in sys.argv:
   def parallel_connections():
    page.evaluate("window.__qa.delays['probe:qa-a']=1200;window.__qa.failures['probe:qa-b']='Timed out while waiting for handshake'")
    card('A').get_by_role('button',name='测试连接',exact=True).click();expect(card('A').get_by_role('button',name='正在处理…',exact=True)).to_be_disabled()
    expect(card('B').get_by_role('button',name='测试连接',exact=True)).to_be_enabled();card('B').get_by_role('button',name='测试连接',exact=True).click()
    expect(card('B').get_by_role('alert')).to_contain_text('SSH 握手超时');expect(card('A')).not_to_contain_text('握手超时')
    expect(card('A')).to_contain_text('认证：已通过');expect(card('B')).to_contain_text('SSH 握手超时')
    assert len([c for c in calls('probe') if c['id']=='qa-a'])==1
   run('并行连接、错误隔离与独立加载',parallel_connections)
   def add_edit_remove():
    click('添加服务器');page.get_by_label('自定义名字',exact=True).fill('新增 QA');page.get_by_label('IP 或主机名',exact=True).fill('192.0.2.33');page.get_by_label('用户名',exact=True).fill('qa');page.get_by_label('密码',exact=True).fill('synthetic-qa-password');click('保存并连接')
    target=page.get_by_label('服务器 新增 QA',exact=True);expect(target).to_contain_text('首次连接：确认主机指纹');assert not [c for c in calls('authenticate') if c['id']=='qa-3']
    target.get_by_role('button',name='信任此主机并测试连接',exact=True).click();expect(target).to_contain_text('认证：已通过')
    target.get_by_label('允许智能体使用 新增 QA',exact=True).check();expect(target.get_by_label('允许智能体使用 新增 QA',exact=True)).to_be_checked()
    target.get_by_label('编辑服务器',exact=True).click();expect(page.get_by_label('密码',exact=True)).to_have_value('');page.get_by_label('自定义名字',exact=True).fill('改名 QA');click('保存并连接')
    target=page.get_by_label('服务器 改名 QA',exact=True);expect(target).to_contain_text('认证：已通过')
    page.once('dialog',lambda dialog:dialog.accept());target.get_by_label('删除服务器',exact=True).click();expect(target).to_have_count(0)
    assert 'synthetic-qa-password' not in json.dumps(calls('save'))
   run('新增、指纹确认、授权、编辑空密码、删除配置',add_edit_remove)
   def key_auth_and_handoff():
    click('添加服务器');page.get_by_label('自定义名字',exact=True).fill('私钥 QA');page.get_by_label('IP 或主机名',exact=True).fill('192.0.2.44');page.get_by_label('用户名',exact=True).fill('key-user');page.get_by_label('认证方式').select_option('key')
    page.locator('input[type=file]').set_input_files({'name':'synthetic-key','mimeType':'text/plain','buffer':b'synthetic-key-not-a-real-secret'})
    expect(page.get_by_role('textbox',name='SSH 私钥',exact=True)).to_have_value('synthetic-key-not-a-real-secret');page.get_by_label('私钥口令（可选）',exact=True).fill('synthetic-passphrase');click('保存并连接')
    target=page.get_by_label('服务器 私钥 QA',exact=True);expect(target).to_contain_text('首次连接：确认主机指纹');target.get_by_role('button',name='取消',exact=True).click();expect(target).not_to_contain_text('首次连接：确认主机指纹')
    target.get_by_label('允许智能体使用 私钥 QA',exact=True).click();expect(target.get_by_label('允许智能体使用 私钥 QA',exact=True)).not_to_be_checked();target.get_by_role('button',name='信任此主机并测试连接',exact=True).click();expect(target.get_by_label('允许智能体使用 私钥 QA',exact=True)).to_be_checked()
    page.once('dialog',lambda dialog:dialog.accept());target.get_by_label('删除服务器',exact=True).click();expect(target).to_have_count(0)
    click('在对话中使用所选服务器');assert page.evaluate('window.__qa.navigation')=='chat';assert calls('session.prompt');assert 'synthetic-key' not in json.dumps(calls('save'))
   run('私钥文件导入、口令草稿、取消指纹、再授权及模型交接',key_auth_and_handoff)
   def directory_browser():
    tab('服务器文件');expect(page.get_by_label('服务器目录路径',exact=True)).to_have_value('/home/qa');expect(page.get_by_text('已加载 200 项',exact=True)).to_be_visible()
    click('加载更多');expect(page.get_by_text('已加载 400 项',exact=True)).to_be_visible();click('加载更多');expect(page.get_by_text('已加载 431 项',exact=True)).to_be_visible()
    page.get_by_label('筛选已加载文件',exact=True).fill('文件 429');expect(page.get_by_role('button',name='文件 429.txt',exact=True)).to_be_visible()
    page.get_by_label('复制路径 文件 429.txt',exact=True).click();assert page.evaluate('navigator.clipboard.readText()')=='/home/qa/文件 429.txt'
    page.get_by_label('筛选已加载文件',exact=True).fill('');page.get_by_role('checkbox',name='显示隐藏文件',exact=True).check();expect(page.get_by_role('button',name='.hidden',exact=True)).to_be_visible()
    click('data');expect(page.get_by_label('服务器目录路径',exact=True)).to_have_value('/home/qa/data');click('上一级');expect(page.get_by_label('服务器目录路径',exact=True)).to_have_value('/home/qa')
    page.get_by_label('后退',exact=True).click();expect(page.get_by_label('服务器目录路径',exact=True)).to_have_value('/home/qa/data');page.get_by_label('前进',exact=True).click();expect(page.get_by_label('服务器目录路径',exact=True)).to_have_value('/home/qa')
    page.get_by_label('服务器目录路径',exact=True).fill('/denied');click('进入');expect(page.get_by_role('alert')).to_contain_text('没有完成此操作的权限')
    click('家目录');expect(page.get_by_role('alert')).to_have_count(0)
    page.get_by_label('服务器目录路径',exact=True).fill('/missing');click('进入');expect(page.get_by_role('alert')).to_contain_text('不存在');click('家目录')
    no_overflow();page.screenshot(path=str(OUT/'files.png'),full_page=True)
   run('目录、430文件分页、隐藏、筛选、复制、路径错误恢复',directory_browser)
   def resources():
    tab('资源监控');expect(page.get_by_label('服务器资源概览')).to_be_visible();expect(page.get_by_role('meter',name='CPU 占用',exact=True)).to_have_attribute('aria-valuenow','15')
    page.get_by_text('系统与硬件详情',exact=False).click();expect(page.get_by_text('QA CPU',exact=True)).to_be_visible();expect(page.get_by_text('磁盘空间',exact=True)).to_be_visible();click('刷新');assert calls('metrics')
    page.get_by_label('服务器账号',exact=True).select_option('qa-b');expect(page.get_by_label('服务器资源概览')).to_be_visible();assert calls('metrics')[-1]['id']=='qa-b';page.get_by_label('服务器账号',exact=True).select_option('qa-a');expect(page.get_by_label('服务器资源概览')).to_be_visible()
    page.screenshot(path=str(OUT/'resources.png'),full_page=True)
   run('资源、磁盘、刷新与布局',resources)
   def environments():
    tab('环境管理');expect(page.get_by_text('existing-r',exact=True)).to_be_visible();assert not calls('workflow_prepare')
    step=page.locator('fieldset input[type=checkbox]:not([disabled])').first;step.check();click('检查所需软件');expect(page.get_by_text('软件检查完成；参考数据和输入配置在生成分析计划时检查。',exact=True)).to_be_visible()
    click('配置所选步骤');expect(page.get_by_role('button',name='正在配置…',exact=True)).to_be_disabled();assert len(calls('workflow_prepare')[-1]['body']['steps'])==1
    page.evaluate("window.__qa.prepareState='complete'");expect(page.get_by_text('最近一次配置已完成',exact=True)).to_be_visible(timeout=8000)
    page.get_by_text('查看安装日志',exact=True).click();expect(page.get_by_text('test installation log',exact=True)).to_be_visible();page.screenshot(path=str(OUT/'environments.png'),full_page=True)
    page.get_by_text('基础环境高级设置',exact=True).click();click('授权安装');expect(page.get_by_role('dialog',name='服务器环境安装',exact=True)).to_be_visible()
    page.get_by_label('服务器账号密码（需有 sudo 权限）').fill('synthetic-sudo');page.evaluate("window.__qa.failures['install_environment']='sudo: permission denied'");click('授权安装并继续配置');expect(page.get_by_role('dialog')).to_contain_text('安装未完成');page.get_by_role('dialog').locator('summary').filter(has_text='查看原因').click();expect(page.get_by_role('dialog')).to_contain_text('需要管理员权限');click('确认结果并关闭');expect(page.get_by_role('dialog')).to_have_count(0)
   run('环境清单、按步骤配置、日志、管理员失败恢复',environments)
   def tasks():
    tab('任务与调度');expect(page.get_by_text('测试运行中任务 · 分析中',exact=True)).to_be_visible();assert page.get_by_text('高级设置：手动提交任务',exact=True).count()==0
    page.get_by_role('button',name='查看日志',exact=True).first.click();expect(page.get_by_text('test task log: completed safely',exact=True)).to_be_visible();click('取消任务');expect(page.get_by_text('测试运行中任务 · 已取消',exact=True)).to_be_visible()
    page.get_by_role('button',name='下载结果',exact=True).first.click();expect(page.get_by_role('status').filter(has_text='结果已下载到')).to_be_visible();page.screenshot(path=str(OUT/'tasks.png'),full_page=True)
   run('任务、日志、取消、结果下载与移除手动入口',tasks)
   def transfers():
    tab('跨服务器数据');expect(page.get_by_text('数据传输记录',exact=True)).to_be_visible();expect(page.get_by_role('alert')).to_contain_text('连接已中断');click('核对并继续传输');expect(page.get_by_role('button',name='核对并继续传输',exact=True)).to_have_count(0)
    page.get_by_label('复制到').select_option('qa-b');click('开始复制');expect(page.get_by_role('status').filter(has_text='传输已校验完成')).to_be_visible();assert calls('transfer')[-1]['body']['target']=='qa-b'
    page.screenshot(path=str(OUT/'transfers.png'),full_page=True)
   run('传输失败提示、恢复及复制目标',transfers)
   def workspace():
    tab('数据保护与审计');expect(page.get_by_text('项目分析文件',exact=True)).to_be_visible();click('工作文件');expect(page.get_by_role('button',name='note.txt',exact=True)).to_be_visible()
    click('note.txt');expect(page.get_by_text('test file content',exact=True)).to_be_visible();click('新建文件夹');dialog=page.get_by_role('dialog',name='目录管理',exact=True);dialog.get_by_label('名称',exact=True).fill('QA folder');dialog.get_by_role('button',name='确认',exact=True).click();expect(page.get_by_role('button',name='QA folder',exact=True)).to_be_visible()
    row=page.locator('article').filter(has=page.get_by_role('button',name='QA folder',exact=True));row.get_by_role('button',name='重命名',exact=True).click();dialog.get_by_label('名称',exact=True).fill('QA renamed');dialog.get_by_role('button',name='确认',exact=True).click();expect(page.get_by_role('button',name='QA renamed',exact=True)).to_be_visible()
    row=page.locator('article').filter(has=page.get_by_role('button',name='QA renamed',exact=True));row.get_by_role('button',name='移动',exact=True).click();dialog.get_by_label('文件分类').select_option('references');dialog.get_by_role('button',name='确认',exact=True).click();expect(page.get_by_role('button',name='QA renamed',exact=True)).to_have_count(0)
    click('参考文件');expect(page.get_by_role('button',name='QA renamed',exact=True)).to_be_visible();page.get_by_label('搜索工作文件',exact=True).fill('QA renamed');click('搜索');expect(page.get_by_role('button',name='QA renamed',exact=True)).to_be_visible()
    row=page.locator('article').filter(has=page.get_by_role('button',name='QA renamed',exact=True));row.get_by_role('button',name='移入回收站',exact=True).click();click('确认移入');expect(page.get_by_text('尚未删除：请在上方“删除提醒”核对范围后确认。',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='已知悉范围，确认移入回收站',exact=True)).to_be_visible(timeout=12000);click('已知悉范围，确认移入回收站');click('回收站');expect(page.get_by_role('button',name='恢复',exact=True)).to_be_visible();click('恢复');expect(page.get_by_text('已恢复到原来的位置。',exact=True)).to_be_visible()
    click('明确授权覆盖此文件');expect(page.get_by_text('已写入，覆盖前的原文件备份已保留在本次对话的工作文件夹。',exact=True)).to_be_visible()
    page.get_by_text('管理保护设置',exact=True).click();click('保存保护范围');expect(page.get_by_role('status').filter(has_text='只读输入和镜像范围已保存')).to_be_visible();assert calls('configure');page.screenshot(path=str(OUT/'protection.png'),full_page=True)
   run('目录新建、重命名、移动、搜索、递归提醒、回收恢复、覆盖及审计',workspace)
   def stability():
    names=['服务器文件','资源监控','环境管理','任务与调度','跨服务器数据','数据保护与审计','SSH 连接']
    for i in range(70):
     tab(names[i%len(names)]);expect(page.get_by_role('navigation',name='服务器管理栏目')).to_be_visible();assert page.locator('body').inner_text().strip()
    tab('SSH 连接');expect(card('A')).to_be_visible()
    for width,height in [(1280,800),(900,700),(768,1024)]:
     page.set_viewport_size({'width':width,'height':height});no_overflow();page.screenshot(path=str(OUT/f'layout-{width}.png'),full_page=True)
    assert not errors,errors
   run('70次栏目切换、三个窗口宽度、脚本错误与布局稳定性',stability)
 finally:
  (OUT/'console.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding='utf-8')
  (OUT/'result.json').write_text(json.dumps({'groups':results,'pageErrors':errors,'mode':'production React components, real headless Edge, controlled isolated service fixtures','desktopGuiOperated':False},ensure_ascii=False,indent=2),encoding='utf-8')
  context.tracing.stop(path=str(OUT/'trace.zip'));browser.close()
print(json.dumps({'groups':len(results),'passed':sum(r['passed'] for r in results),'failed':sum(not r['passed'] for r in results),'errors':errors},ensure_ascii=False))
if any(not r['passed'] for r in results):raise SystemExit(1)
