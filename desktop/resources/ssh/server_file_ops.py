"""Account-permitted writes and copies with registered-data protection and backups."""
import hashlib,json,os,shutil,stat,tempfile,time,uuid
from pathlib import Path

def signature(info):return {k:getattr(info,'st_'+k) for k in ('dev','ino','size','mtime_ns','ctime_ns')}

def destination(r,value):
    if not isinstance(value,str) or not value or '\0' in value:raise ValueError('请输入服务器绝对路径')
    p=Path(value).expanduser()
    if not p.is_absolute():raise ValueError('写入服务器文件需要绝对路径')
    import protected_paths
    protected_paths.check(r,p)
    p=p.resolve(strict=False)
    return p

def request_dir(r):
    p=r.root()/'write-requests'
    if p.is_symlink():raise ValueError('写入请求目录不正确')
    p.mkdir(exist_ok=True,mode=0o700);return p

def record(r,identifier):
    p=request_dir(r)/r.identifier(identifier)
    if p.is_symlink() or (p/'request.json').is_symlink():raise ValueError('写入请求不正确')
    if (p/'proposed.txt').is_symlink():raise ValueError('拟写入文件不能是链接')
    return p,json.loads((p/'request.json').read_text())

def file_source(r,body):
    import server_workspace as storage
    p=storage.read_path(r,body.get('source'))
    if not (p.is_file() or p.is_dir()):raise ValueError('请提供要复制的文件或目录路径')
    return p

def tree_signature(path):
    digest=hashlib.sha256()
    def add(p):
        digest.update(json.dumps([str(p.relative_to(path)),signature(p.lstat())],sort_keys=True).encode())
    add(path)
    def unreadable(error):raise error
    for folder,dirs,files in os.walk(path,followlinks=False,onerror=unreadable):
        dirs.sort();files.sort()
        for name in dirs+files:add(Path(folder)/name)
    return digest.hexdigest()

def check_tree(r,path):
    import protected_paths
    registered=protected_paths.entries(r)
    protected_paths.check(r,path,'overwrite',recursive=True,registered=registered)
    if path.is_dir():
        for folder,dirs,files in os.walk(path,followlinks=False):
            for name in dirs+files:protected_paths.check(r,Path(folder)/name,'overwrite',recursive=True,registered=registered)

def written(r,body,path,approved,backup):
    context=body.get('context');db=r.database()
    db.execute('INSERT OR REPLACE INTO workspace_external_files VALUES(?,?,?)',(str(path),json.dumps(context),time.time()))
    r.audit(db,'server.file_written',{'path':str(path),'source':body.get('source'),'context':context,'overwritten':approved,'backup':str(backup) if backup else None})
    return {'path':str(path),'created':not approved,'overwritten':approved,'backup':str(backup) if backup else None}

def copy_directory(r,body,source,p,approved):
    check_tree(r,p)
    if p==source or source in p.parents or p in source.parents:raise ValueError('源目录与目标目录不能相互包含')
    if tree_signature(source)!=body['sourceTreeSignature']:raise ValueError('要复制的源文件已变化，请重新复制')
    p.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.omaax-copy-',dir=p.parent));proposed=stage/'content';backup=None
    try:
        shutil.copytree(source,proposed,symlinks=True)
        if tree_signature(source)!=body['sourceTreeSignature']:raise ValueError('源目录在复制时发生变化，目标未覆盖')
        check_tree(r,p)
        if approved:
            current=tree_signature(p) if p.is_dir() else signature(p.stat())
            if current!=body.get('previousTreeSignature',body['previous']):raise ValueError('目标文件已变化，原文件未覆盖；请重新提交')
            import workspace_context
            backup=workspace_context.base(r,body.get('context'))/'files/overwrite-backups'/body['id']/p.name
            if p==backup or p in backup.parents:raise ValueError('备份位置位于目标目录内，无法安全替换该目录')
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(p),str(backup))
        elif p.exists() or p.is_symlink():raise ValueError('目标已变化，未覆盖；请重新提交')
        try:proposed.rename(p)
        except BaseException:
            if backup and not p.exists():shutil.move(str(backup),str(p))
            raise
    finally:shutil.rmtree(stage)
    return written(r,body,p,approved,backup)

def perform(r,body,folder,approved=False):
    p=destination(r,body['path']);source=Path(body['source']) if body.get('source') else folder/'proposed.txt'
    if body.get('source'):
        source=file_source(r,body)
        if signature(source.stat())!=body['sourceSignature']:raise ValueError('要复制的源文件已变化，请重新提出复制请求')
        if source.is_dir():return copy_directory(r,body,source,p,approved)
    if p.exists() and not stat.S_ISREG(p.stat().st_mode):raise ValueError('文本或文件写入的目标必须是普通文件；复制目录请提供目录源')
    p.parent.mkdir(parents=True,exist_ok=True)
    flags=os.O_RDWR|(os.O_CREAT|os.O_EXCL if not approved else 0)|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(p,flags,0o600);backup=None
    with os.fdopen(fd,'r+b') as output:
        if approved:
            if signature(os.fstat(output.fileno()))!=body['previous']:raise ValueError('目标文件已变化，原文件未覆盖；请重新提交')
            import workspace_context
            backup=workspace_context.base(r,body.get('context'))/'files/overwrite-backups'/body['id']/p.name
            if body['previous']['size']>r.disk_available():raise ValueError('保存原文件备份的空间不足，未覆盖原文件')
            backup.parent.mkdir(parents=True,exist_ok=True)
            with backup.open('xb') as saved:shutil.copyfileobj(output,saved,1048576)
            if signature(os.fstat(output.fileno()))!=body['previous']:raise ValueError('原文件在备份时发生变化，未覆盖')
            output.seek(0);output.truncate(0)
        try:
            with source.open('rb') as incoming:shutil.copyfileobj(incoming,output,1048576)
            if body.get('source') and signature(source.stat())!=body['sourceSignature']:raise ValueError('源文件在复制时发生变化，请重新复制')
            output.flush();os.fsync(output.fileno())
        except BaseException:
            if backup:
                output.seek(0);output.truncate(0)
                with backup.open('rb') as original:shutil.copyfileobj(original,output,1048576)
                output.flush();os.fsync(output.fileno())
            raise
    return written(r,body,p,approved,backup)

def submit(r,body,copy=False):
    import workspace_context
    p=destination(r,body.get('path'));identifier=str(uuid.uuid4());folder=request_dir(r)/identifier;folder.mkdir(mode=0o700)
    request={'id':identifier,'path':str(p),'context':workspace_context.normalize(body.get('context')),'created':time.time(),'state':'running'}
    workspace_context.base(r,request['context'])
    if copy:
        source=file_source(r,body)
        if p==source:raise ValueError('源文件和目标文件相同')
        request.update(source=str(source),sourceSignature=signature(source.stat()))
        if source.is_dir():request['sourceTreeSignature']=tree_signature(source)
    else:
        content=body.get('content')
        if isinstance(content,(dict,list)):content=json.dumps(content,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        if not isinstance(content,str) or '\0' in content:raise ValueError('请提供文本或结构化配置内容')
        (folder/'proposed.txt').write_text(content,encoding='utf-8',newline='');request['contentSha256']=hashlib.sha256(content.encode()).hexdigest()
    if p.exists():request['previous']=signature(p.stat())
    if p.is_dir():request['previousTreeSignature']=tree_signature(p)
    request['digest']=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    (folder/'request.json').write_text(json.dumps(request),encoding='utf-8')
    try:
        result=perform(r,request,folder,approved='previous' in request)
    except BaseException as error:
        request.update(state='failed',error=str(error));(folder/'request.json').write_text(json.dumps(request))
        r.audit(r.database(),'server.file_write_failed',{'path':str(p),'id':identifier,'error':str(error)})
        raise
    request['state']='complete';(folder/'request.json').write_text(json.dumps(request));return result

def dispatch(r,action,body,actor):
    fields={'server_write':{'path','content','context'},'server_copy':{'path','source','context'},'server_write_requests':set(),'server_write_confirm':{'id','digest'},'server_write_reject':{'id'}}
    if not isinstance(body,dict) or set(body)-fields[action]:raise ValueError('服务器写入参数不正确')
    if action in ('server_write','server_copy'):return submit(r,body,action=='server_copy')
    if actor!='user':raise ValueError('覆盖已有文件必须由用户在桌面明确批准，模型不能自行批准')
    if action=='server_write_requests':
        rows=[]
        for p in request_dir(r).iterdir():
            if p.is_symlink() or not (p/'request.json').is_file():continue
            _,item=record(r,p.name)
            if item['state']=='pending':
                row={k:item.get(k) for k in ('id','path','context','created','digest','previous','source')}
                proposed=p/'proposed.txt'
                if proposed.is_file():row['preview']=proposed.read_text(encoding='utf-8')[:16384];row['proposedBytes']=proposed.stat().st_size
                rows.append(row)
        return sorted(rows,key=lambda item:-item['created'])[:100]
    folder,item=record(r,body['id'])
    if item['state']!='pending':raise ValueError('此请求已处理，请刷新列表')
    if action=='server_write_reject':item['state']='rejected';result={'rejected':True}
    else:
        if body.get('digest')!=item['digest']:raise ValueError('写入请求版本不匹配')
        signed={k:v for k,v in item.items() if k!='digest'}
        if hashlib.sha256(json.dumps(signed,sort_keys=True).encode()).hexdigest()!=item['digest']:raise ValueError('写入请求已变化，请重新提交')
        if not item.get('source') and hashlib.sha256((folder/'proposed.txt').read_bytes()).hexdigest()!=item['contentSha256']:raise ValueError('拟写入内容已变化，禁止覆盖')
        result=perform(r,item,folder,approved=True);item['state']='complete'
    (folder/'request.json').write_text(json.dumps(item));return result
