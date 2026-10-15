"""Account-permitted writes; existing-file replacement requires a desktop approval."""
import hashlib,json,os,shutil,stat,time,uuid
from pathlib import Path

def signature(info):return {k:getattr(info,'st_'+k) for k in ('dev','ino','size','mtime_ns','ctime_ns')}

def destination(r,value):
    import server_workspace as storage
    if not isinstance(value,str) or not value or any(c in value for c in ('\0','\n','\r')):raise ValueError('请输入服务器绝对路径')
    p=Path(value).expanduser()
    if not p.is_absolute():raise ValueError('写入服务器文件需要绝对路径')
    p=p.resolve(strict=False)
    private=[Path.home()/name for name in ('.ssh','.aws','.kube','.docker')]+[Path('/proc'),Path('/sys'),Path('/dev')]
    if any(p==d or d in p.parents for d in private) or p.name in ('.env','.netrc','.git-credentials','id_rsa','id_ed25519'):raise ValueError('不能写入凭据或系统特殊文件')
    control=r.ROOT.resolve();workspace=storage.workspace(r)
    if (p==control or control in p.parents) and not workspace in p.parents:raise ValueError('不能写入应用内部控制文件')
    if p.exists() and not p.is_file():raise ValueError('目标不是普通文件')
    if p.exists() and storage.busy_path(r,p):raise ValueError('此工作文件正在被任务使用，请等待任务结束')
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
    if not p.is_file():raise ValueError('请提供要复制的文件路径')
    return p

def perform(r,body,folder,approved=False):
    import server_workspace as storage
    p=destination(r,body['path']);source=Path(body['source']) if body.get('source') else folder/'proposed.txt'
    if body.get('source'):
        source=file_source(r,body)
        if signature(source.stat())!=body['sourceSignature']:raise ValueError('要复制的源文件已变化，请重新提出复制请求')
    p.parent.mkdir(parents=True,exist_ok=True)
    flags=os.O_RDWR|(os.O_CREAT|os.O_EXCL if not approved else 0)|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(p,flags,0o600);backup=None
    with os.fdopen(fd,'r+b') as output:
        if approved:
            if signature(os.fstat(output.fileno()))!=body['previous']:raise ValueError('目标文件已变化，原文件未覆盖；请重新确认')
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
    context=body.get('context');db=r.database()
    db.execute('INSERT OR REPLACE INTO workspace_external_files VALUES(?,?,?)',(str(p),json.dumps(context),time.time()))
    r.audit(db,'server.file_written',{'path':str(p),'context':context,'overwriteApproved':approved,'backup':str(backup) if backup else None})
    return {'path':str(p),'created':not approved,'overwritten':approved,'backup':str(backup) if backup else None}

def submit(r,body,copy=False):
    import workspace_context
    p=destination(r,body.get('path'));identifier=str(uuid.uuid4());folder=request_dir(r)/identifier;folder.mkdir(mode=0o700)
    request={'id':identifier,'path':str(p),'context':workspace_context.normalize(body.get('context')),'created':time.time(),'state':'pending'}
    workspace_context.base(r,request['context'])
    if copy:
        source=file_source(r,body)
        if p==source:raise ValueError('源文件和目标文件相同')
        request.update(source=str(source),sourceSignature=signature(source.stat()))
    else:
        content=body.get('content')
        if isinstance(content,(dict,list)):content=json.dumps(content,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        if not isinstance(content,str) or '\0' in content or len(content.encode())>1048576:raise ValueError('请提供不超过 1 MB 的文本或结构化配置内容')
        (folder/'proposed.txt').write_text(content,encoding='utf-8',newline='');request['contentSha256']=hashlib.sha256(content.encode()).hexdigest()
    if p.exists():request['previous']=signature(p.stat())
    request['digest']=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    (folder/'request.json').write_text(json.dumps(request),encoding='utf-8')
    if p.exists():
        r.audit(r.database(),'server.overwrite_requested',{'id':identifier,'path':str(p),'context':request['context']})
        return {'id':identifier,'path':str(p),'requiresApproval':True,'message':'文件已存在，尚未覆盖。请用户在服务器管理的文件写入请求中明确批准，或改用新文件名。'}
    result=perform(r,request,folder);request['state']='complete';(folder/'request.json').write_text(json.dumps(request));return result

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
