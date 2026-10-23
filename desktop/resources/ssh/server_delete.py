"""Recoverable deletion; only registered original-data paths are policy-protected."""
import errno,glob,hashlib,json,os,shutil,stat,time,uuid
from pathlib import Path
import server_workspace as storage
import workspace_context

LIMIT=10_000_000_000

def destination(r,value,existing=True,registered=None):
    if not isinstance(value,str) or not value or '\0' in value:raise ValueError('请提供明确的服务器绝对路径')
    p=Path(value).expanduser()
    if not p.is_absolute():raise ValueError('删除需要绝对路径')
    p=p.parent.resolve(strict=False)/p.name
    import protected_paths
    protected_paths.check(r,p,operation='delete',recursive=True,registered=registered)
    if existing and not p.exists() and not p.is_symlink():raise ValueError('删除目标不存在')
    return p

def snapshot(r,paths):
    import protected_paths
    registered=protected_paths.entries(r)
    total=0;count=0;digest=hashlib.sha256()
    def add(p):
        nonlocal total,count
        protected_paths.check(r,p,'delete',recursive=True,registered=registered)
        info=p.lstat();count+=1
        if stat.S_ISREG(info.st_mode):total+=info.st_size
        digest.update(json.dumps([str(p),info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_mode]).encode())
    for p in paths:
        destination(r,str(p),registered=registered)
        add(p)
        if p.is_dir() and not p.is_symlink():
            for folder,dirs,files in os.walk(p,followlinks=False):
                dirs.sort();files.sort()
                for name in dirs+files:
                    child=Path(folder)/name
                    add(child)
    return {'bytes':total,'entries':count,'signature':digest.hexdigest()}

def folder(r):
    p=r.root()/'delete-requests'
    if p.is_symlink():raise ValueError('删除请求目录不能是链接')
    p.mkdir(mode=0o700,exist_ok=True);return p

def save(r,item):
    p=folder(r)/(r.identifier(item['id'])+'.json');temp=p.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8') as f:json.dump(item,f,ensure_ascii=False)
    temp.replace(p)

def content_hash(p):
    digest=hashlib.sha256()
    items=[p]
    if p.is_dir() and not p.is_symlink():
        for folder,dirs,files in os.walk(p,followlinks=False):items.extend(Path(folder)/name for name in sorted(dirs+files))
    for child in sorted(items,key=str):
        info=child.lstat();digest.update(json.dumps([str(child.relative_to(p)),stat.S_IFMT(info.st_mode),info.st_size if stat.S_ISREG(info.st_mode) else 0]).encode())
        if child.is_symlink():digest.update(os.readlink(child).encode())
        elif child.is_file():
            with child.open('rb') as f:
                while block:=f.read(1024*1024):digest.update(block)
    return digest.hexdigest()

def execute(r,item):
    import protected_paths
    registered=protected_paths.entries(r)
    paths=[destination(r,p,registered=registered) for p in item['paths']]
    trash=storage.workspace(r)/'.trash'
    if any(p==trash or p in trash.parents for p in paths):raise ValueError('回收站位于待删除目录内，无法将目录移动到自身内部；原文件未删除')
    if snapshot(r,paths)!=item['snapshot']:raise ValueError('删除范围或内容已变化，请重新提交；未删除任何文件')
    cross=[p for p in paths if p.lstat().st_dev!=trash.stat().st_dev]
    needed=sum(p.lstat().st_size if p.is_file() and not p.is_symlink() else sum(c.lstat().st_size for c in p.rglob('*') if c.is_file() and not c.is_symlink()) if not p.is_symlink() else 0 for p in cross)
    if needed>shutil.disk_usage(trash).free:raise ValueError('跨文件系统移入回收站需要保存完整副本，空间不足；尚未删除任何文件')
    staged=[];moved=[];started_cross=False
    try:
        for p in paths:
            token=str(uuid.uuid4());target=trash/token;target.mkdir(mode=0o700)
            (target/'record.json').write_text(json.dumps({'path':str(p),'absolute':True,'deleted':time.time(),'request':item['id'],'ready':False}))
            if p in cross:
                content=target/'content'
                if p.is_symlink():content.symlink_to(os.readlink(p),target_is_directory=p.is_dir())
                elif p.is_dir():shutil.copytree(p,content,symlinks=True)
                else:shutil.copy2(p,content,follow_symlinks=False)
                if content_hash(p)!=content_hash(content):raise ValueError('回收副本校验未通过，原位置未清理')
            staged.append((p,target,token))
        if snapshot(r,paths)!=item['snapshot']:raise ValueError('备份期间文件已变化，原位置未清理，请重新预览')
        for _,target,_ in staged:
            record=json.loads((target/'record.json').read_text());record['ready']=True;(target/'record.json').write_text(json.dumps(record))
        for p,target,token in staged:
            if p not in cross:p.rename(target/'content');moved.append((p,target,token))
        for p,target,token in staged:
            if p in cross:
                started_cross=True
                if p.is_dir() and not p.is_symlink():shutil.rmtree(p)
                else:p.unlink()
    except BaseException:
        for original,target,_ in reversed(moved):
            if not original.exists() and not original.is_symlink():(target/'content').rename(original)
        if started_cross:
            item.update(state='partial',trashIds=[token for p,_,token in staged if p in cross]);save(r,item)
            raise ValueError('部分清理未完成。完整的跨盘回收副本仍保留在回收站，请核对原位置后恢复；不自动重复删除')
        raise
    item.update(state='complete',trashIds=[token for _,_,token in staged]);save(r,item)
    r.audit(r.database(),'server.deleted',{'id':item['id'],'paths':item['paths'],'bytes':item['snapshot']['bytes'],'trashIds':item['trashIds']})
    return {'deleted':item['paths'],'trashIds':item['trashIds'],'trashId':item['trashIds'][0] if len(staged)==1 else None,'recoverable':True}

def submit(r,body,notice=False):
    import protected_paths
    if set(body)-{'path','recursive','context'}:raise ValueError('删除参数不正确')
    value=body.get('path');recursive=body.get('recursive',False)
    if type(recursive) is not bool or not isinstance(value,str):raise ValueError('请提供路径和是否递归')
    expanded=os.path.expanduser(value);wild=glob.has_magic(expanded)
    if not Path(expanded).is_absolute():raise ValueError('删除需要服务器绝对路径')
    registered=protected_paths.entries(r)
    matched=[destination(r,p,registered=registered) for p in (sorted(glob.glob(expanded,recursive=recursive)) if wild else [expanded])]
    paths=[]
    for p in sorted(set(matched),key=lambda p:(len(p.parts),str(p))):
        if not any(parent in p.parents and not parent.is_symlink() for parent in paths):paths.append(p)
    if not paths:raise ValueError('未匹配到要删除的路径')
    if any(p.is_dir() and not p.is_symlink() for p in paths) and not recursive:raise ValueError('删除目录需要设置 recursive 为 true')
    summary=snapshot(r,paths);reasons=[]
    if wild:reasons.append('使用通配符，将处理所有匹配文件')
    if recursive:reasons.append('递归处理目录及其中所有文件')
    if summary['bytes']>LIMIT:reasons.append('删除数据超过 10 GB')
    item={'id':str(uuid.uuid4()),'paths':[str(p) for p in paths],'snapshot':summary,'warnings':reasons,'context':workspace_context.normalize(body.get('context')),'created':time.time(),'state':'running','policy':'registered-paths-v1'}
    item['digest']=hashlib.sha256(json.dumps(item,sort_keys=True).encode()).hexdigest();save(r,item)
    try:return execute(r,item)
    except BaseException as error:
        if item['state']!='partial':item['state']='failed'
        item['error']=str(error);save(r,item)
        r.audit(r.database(),'server.delete_failed',{'id':item['id'],'paths':item['paths'],'error':str(error),'state':item['state']})
        raise


def dispatch(r,action,body,actor):
    if action=='server_delete':return submit(r,body,notice=actor=='agent')
    if actor!='user':raise ValueError('删除提醒必须由用户确认，模型不能自行确认')
    if action=='server_delete_requests':
        if body:raise ValueError('请求列表不接受参数')
        items=[]
        for p in folder(r).glob('*.json'):
            if p.is_symlink():continue
            item=json.loads(p.read_text())
            if item['state']=='pending':items.append(item)
        return sorted(items,key=lambda i:-i['created'])[:100]
    if set(body)-{'id','digest'}:raise ValueError('删除确认参数不正确')
    p=folder(r)/(r.identifier(body.get('id'))+'.json')
    lock_path=p.with_suffix('.lock')
    if lock_path.is_symlink():raise ValueError('删除请求锁不能是链接')
    with lock_path.open('a') as lock:
        if os.name=='posix':
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX)
        return process(r,action,body,p)

def process(r,action,body,p):
    if p.is_symlink():raise ValueError('删除请求不能是链接')
    item=json.loads(p.read_text())
    if item['state']!='pending':raise ValueError('请求已处理，请刷新')
    if action=='server_delete_reject':item['state']='rejected';save(r,item);return {'rejected':True}
    if action not in ('server_delete_confirm','server_delete_notified') or body.get('digest')!=item['digest']:raise ValueError('删除请求版本不匹配')
    signed={k:v for k,v in item.items() if k!='digest'}
    if hashlib.sha256(json.dumps(signed,sort_keys=True).encode()).hexdigest()!=item['digest']:raise ValueError('删除请求已变化，请重新预览')
    if action=='server_delete_notified':
        if item.get('policy')!='notice-v1':raise ValueError('旧请求仍需原有确认，请重新预览')
        item['notifiedAt']=time.time()
        r.audit(r.database(),'server.delete_notice_displayed',{'id':item['id'],'digest':item['digest'],'paths':item['paths'],'bytes':item['snapshot']['bytes']})
    return execute(r,item)
