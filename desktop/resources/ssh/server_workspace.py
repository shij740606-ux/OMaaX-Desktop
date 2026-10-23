"""Account-permission file access, project filing and registered-path protection."""
import protected_paths
import csv,hashlib,io,json,os,re,shutil,stat,subprocess,time,uuid
from pathlib import Path

def workspace(r):
    base=r.workspace_root()
    if any(p.is_symlink() for p in [base,*base.parents]):raise ValueError('工作文件夹不能是链接')
    if not base.exists():protected_paths.check(r,base,'mkdir')
    base.mkdir(mode=0o700,parents=True,exist_ok=True)
    if os.name=='posix' and base.stat().st_uid!=os.getuid():raise ValueError('工作文件夹不属于当前 SSH 账号')
    for name in ('configs','references','files','results','projects','.trash'):
        p=base/name
        if p.is_symlink():raise ValueError('工作子目录不能是链接')
        if not p.exists():protected_paths.check(r,p,'mkdir')
        p.mkdir(mode=0o700,exist_ok=True)
    return base

def read_path(r,value):
    if not isinstance(value,str) or not value or len(value)>4096 or any(c in value for c in ('\0','\n','\r')):raise ValueError('请输入服务器上的文件或目录路径')
    p=Path(value).expanduser()
    if not p.is_absolute():raise ValueError('请提供服务器上的绝对路径')
    p=p.resolve(strict=True)
    if not (p.is_file() or p.is_dir()) or not os.access(p,os.R_OK|(os.X_OK if p.is_dir() else 0)):raise ValueError('当前 SSH 账号没有读取此路径的权限')
    return p

def managed(r,value,existing=True,delete_link=False,context=None):
    if not isinstance(value,str) or not value or '\\' in value or Path(value).is_absolute() or '..' in Path(value).parts or any(c in value for c in ('\0','\n','\r')):raise ValueError('写入和删除只能使用工作文件夹内的相对路径')
    base=workspace(r)
    if context and Path(value).parts[0] in ('configs','references','files','tasks'):
        import workspace_context
        value=(workspace_context.base(r,context).relative_to(base)/value).as_posix()
    p=base/value
    if existing and not p.exists() and not (delete_link and p.is_symlink()):raise ValueError('工作文件不存在')
    return p

def source(r,db,path):
    folder=read_path(r,str(path))
    if not folder.is_dir():raise ValueError('请选择数据目录')
    name='path_'+hashlib.sha256(str(folder).encode()).hexdigest()[:20]
    db.execute('BEGIN IMMEDIATE')
    cfg=r.config(db);sources=dict(cfg.get('sources',{}));sources[name]=str(folder)
    db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',('sources',json.dumps(sources)));db.commit()
    return name,folder

def files(r,body):
    action=body.get('operation','list');p=read_path(r,body.get('path'))
    if action=='stat':return {'path':str(p),'directory':p.is_dir(),'size':p.stat().st_size,'readOnly':True}
    if action=='read':
        if not p.is_file() or p.stat().st_size>1048576:raise ValueError('文本预览最多 1 MB；大文件请查看文件信息')
        data=p.read_bytes()
        if b'\0' in data:raise ValueError('这是二进制文件，不能作为文本预览')
        return {'path':str(p),'text':data.decode('utf-8-sig'),'readOnly':True}
    if action!='list' or not p.is_dir():raise ValueError('请选择列目录、文件信息或文本预览')
    offset=body.get('offset',0)
    if type(offset) is not int or offset<0:raise ValueError('列表位置不正确')
    rows=[]
    for child in sorted(p.iterdir(),key=lambda x:x.name):
        try:
            target=read_path(r,str(child));info=target.stat();rows.append({'name':child.name,'path':str(child),'directory':target.is_dir(),'size':info.st_size,'linked':child.is_symlink()})
        except (ValueError,OSError):continue
        child_path=child.resolve();root=workspace(r)
        if root in child_path.parents:
            rel=child_path.relative_to(root).as_posix()
            record=r.database().execute('SELECT j.spec,j.created FROM job_locations l JOIN jobs j ON j.id=l.job_id WHERE l.path=?',(rel,)).fetchone()
            if record:
                rows[-1]['displayName']=json.loads(record['spec']).get('title',child.name) if re.fullmatch(r'[a-f0-9-]{36}',child.name) else child.name
                rows[-1]['created']=record['created']
    return {'path':str(p),'files':rows[offset:offset+500],'fileCount':len(rows),'nextOffset':offset+500 if len(rows)>offset+500 else None,'readOnly':True}

def busy_path(r,p):
    for row in r.database().execute("SELECT spec FROM jobs WHERE state IN ('queued','running')"):
        spec=json.loads(row['spec']);directory=r.jobdir(spec['id'])
        if p==directory or directory in p.parents or p in directory.parents:return True
        cfg=r.config(r.database())
        for name in spec.get('sources',[]):
            value=cfg.get('sources',{}).get(name)
            if value:
                used=Path(value).resolve()
                if p==used or p in used.parents or used in p.parents:return True
        for file in spec.get('files',[]):
            used=Path(file['source'])
            if p==used or p in used.parents:return True
    return False

def write_config(r,body):
    value=body.get('path')
    if not isinstance(value,str):raise ValueError('请提供配置文件路径')
    p=Path(value).expanduser() if Path(value).expanduser().is_absolute() else managed(r,value,existing=False,context=body.get('context'))
    content=body.get('content');kind=body.get('format','yaml')
    if kind in ('yaml','json'):
        if not isinstance(content,(dict,list)):raise ValueError('配置必须是结构化对象或列表')
        # JSON is valid YAML and does not require modifying the server Python environment.
        data=json.dumps(content,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    elif kind in ('csv','tsv'):
        columns=body.get('columns');rows=content
        if not isinstance(columns,list) or not columns or not all(isinstance(k,str) for k in columns) or not isinstance(rows,list):raise ValueError('请提供表头和数据行')
        stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=columns,delimiter='\t' if kind=='tsv' else ',');writer.writeheader();writer.writerows(rows);data=stream.getvalue()
    else:raise ValueError('支持 YAML、JSON、CSV 和 TSV 配置表')
    import server_file_ops
    result=server_file_ops.submit(r,{'path':str(p),'content':data,'context':body.get('context')})
    r.audit(r.database(),'workspace.config_saved',{'path':str(p),'format':kind})
    relative=p.relative_to(workspace(r)).as_posix() if workspace(r) in p.parents else None
    return {**result,'path':str(p),'workspaceRelative':relative,'bytes':p.stat().st_size}

def manage(r,body):
    operation=body.get('operation','list');value=body.get('path','files')
    import workspace_context
    if operation=='contexts':return {'items':workspace_context.listings(r)}
    if operation=='external':
        context=body.get('context');rows=[]
        for row in r.database().execute('SELECT * FROM workspace_external_files ORDER BY created DESC'):
            saved=json.loads(row['context'])
            if context and (saved.get('projectId'),saved.get('sessionId'))!=(context.get('projectId'),context.get('sessionId')):continue
            rows.append({'path':row['path'],'context':saved,'created':row['created']})
        return {'files':rows[:500]}
    if operation=='info':
        context=body.get('context');base=workspace_context.base(r,context) if context else workspace(r)
        old=r.root()/'workspace'
        return {'path':str(base),'workspaceRoot':str(workspace(r)),'legacyRoot':str(old) if old!=workspace(r) and old.is_dir() else None,'folders':['configs','references','files','tasks'] if context else ['projects','configs','references','files','results'],'context':context,'originalDataReadOnly':False,'inputPolicy':'preserve-originals'}
    if operation=='trash':
        rows=[]
        for p in (workspace(r)/'.trash').iterdir():
            record=p/'record.json'
            if p.is_symlink() or record.is_symlink() or not record.is_file():continue
            data=json.loads(record.read_text())
            if data.get('ready',True) and ((p/'content').exists() or (p/'content').is_symlink()):rows.append({'trashId':p.name,**data})
        return {'files':sorted(rows,key=lambda row:-row['deleted'])[:500]}
    if value=='legacy' or value.startswith('legacy/'):
        old=r.root()/'workspace'
        if operation not in ('list','read','stat') or old==workspace(r):raise ValueError('旧版目录仅提供查看，不移动原有数据')
        p=r.contained(old,value[7:] if value.startswith('legacy/') else '.')
        return files(r,{'path':str(p),'operation':operation,'offset':body.get('offset',0)})
    p=Path(value).expanduser() if Path(value).expanduser().is_absolute() else managed(r,value,existing=operation not in ('mkdir',),delete_link=operation in ('delete','move','rename'),context=body.get('context'))
    if operation in ('list','read','stat'):return files(r,{'path':str(p),'operation':operation,'offset':body.get('offset',0)})
    if operation=='search':
        query=body.get('query','').strip().lower()
        if not query:raise ValueError('请输入要查找的文件或目录名称')
        rows=[];count=0
        for child in p.rglob('*'):
            count+=1
            if count>20000 or len(rows)>=500:break
            if query not in child.name.lower() or child.is_symlink():continue
            try:
                resolved=read_path(r,str(child));rows.append({'name':child.name,'path':str(child),'directory':resolved.is_dir(),'size':resolved.stat().st_size})
            except (OSError,ValueError):continue
        return {'path':str(p),'files':rows,'truncated':count>20000 or len(rows)>=500}
    if operation in ('mkdir','move','rename','delete'):protected_paths.check(r,p,operation,recursive=operation!='mkdir')
    if operation=='mkdir':
        p.mkdir(parents=True,exist_ok=False);r.audit(r.database(),'workspace.mkdir',{'path':str(p)});return {'path':str(p)}
    if operation in ('move','rename'):
        target=body.get('destination')
        if not isinstance(target,str):raise ValueError('请提供目标路径')
        dest=Path(target).expanduser() if Path(target).expanduser().is_absolute() else managed(r,target,existing=False,context=body.get('context'))
        protected_paths.check(r,dest,'move')
        if dest.exists() or dest.is_symlink():raise ValueError('目标位置已有文件，不能覆盖')
        if p==dest or p in dest.parents:raise ValueError('不能移动到自己的子目录')
        dest.parent.mkdir(parents=True,exist_ok=True)
        workspace_context.relocate(r,p,dest)
        return {'path':str(dest),'moved':True}
    if operation!='delete':raise ValueError('未支持的工作文件操作')
    import server_delete
    return server_delete.submit(r,{'path':str(p),'recursive':p.is_dir(),'context':body.get('context')})

def restore(r,body):
    token=r.identifier(body.get('trashId'));trash=workspace(r)/'.trash'/token
    if trash.is_symlink() or (trash/'record.json').is_symlink() or not (trash/'record.json').is_file():raise ValueError('回收记录不存在')
    record=json.loads((trash/'record.json').read_text())
    if not record.get('ready',True):raise ValueError('回收副本尚未校验完成，不能恢复')
    if record.get('absolute'):
        import server_delete
        dest=server_delete.destination(r,record['path'],existing=False)
    else:dest=managed(r,record['path'],existing=False)
    if dest.exists() or dest.is_symlink():raise ValueError('原位置已有文件，不能覆盖')
    content=trash/'content'
    if not content.exists() and not content.is_symlink():raise ValueError('回收文件不正确')
    protected_paths.check(r,dest,'restore')
    dest.parent.mkdir(parents=True,exist_ok=True)
    try:content.rename(dest)
    except OSError as error:
        import errno,server_delete
        if error.errno!=errno.EXDEV:raise
        if content.is_symlink():dest.symlink_to(os.readlink(content),target_is_directory=content.is_dir())
        elif content.is_dir():shutil.copytree(content,dest,symlinks=True)
        else:
            with content.open('rb') as incoming,dest.open('xb') as output:shutil.copyfileobj(incoming,output,1048576)
        if server_delete.content_hash(content)!=server_delete.content_hash(dest):raise ValueError('恢复副本校验失败，回收站原件仍保留')
        if content.is_dir() and not content.is_symlink():shutil.rmtree(content)
        else:content.unlink()
    r.audit(r.database(),'workspace.restored',{'path':record['path'],'trashId':token})
    return {'path':str(dest),'restored':True}

def script(r,body):
    value=body.get('path');text=body.get('content')
    if not isinstance(value,str) or not isinstance(text,str) or '\0' in text:raise ValueError('请提供脚本路径和文本内容')
    p=Path(value).expanduser() if Path(value).expanduser().is_absolute() else managed(r,value,existing=False,context=body.get('context'))
    import server_file_ops
    result=server_file_ops.submit(r,{'path':str(p),'content':text,'context':body.get('context')})
    r.audit(r.database(),'workspace.script_saved',{'path':str(p),'bytes':len(text.encode())})
    return {**result,'path':str(p),'message':'脚本已保存；可在服务器已有环境或指定环境中执行。'}

def analysis(r,body):
    cfg=r.config(r.database());image=body.get('image')
    if not image:
        env=cfg.get('workflowEnvironments',{}).get(body.get('workflow','rnaseq'),{})
        candidates=[env.get('stageImages',{}).get(body.get('step')),env.get('image'),*cfg.get('images',[])]
        for candidate in dict.fromkeys(candidates):
            if not r.conda_runtime.is_ref(candidate):continue
            try:r.conda_runtime.resolve(r,candidate)
            except (ValueError,OSError):continue
            image=candidate;break
        image=image or r.conda_runtime.HOST_REF
    if image!=r.conda_runtime.HOST_REF and (image not in cfg.get('images',[]) or not r.immutable_image(image)):raise ValueError('计算环境尚未登记；可选择 host:direct 使用服务器已有程序')
    inputs=body.get('inputs',[]);argv=body.get('argv')
    if not isinstance(inputs,list) or not isinstance(argv,list) or not argv or not all(isinstance(s,str) and '\0' not in s for s in argv):raise ValueError('请提供输入路径列表和分析程序参数')
    sources=[];mounts={};file_mounts=[];bindings={}
    for i,value in enumerate(inputs):
        p=read_path(r,value);alias='data'+str(i);target='/input/'+alias
        if p.is_dir():
            name,_=source(r,r.database(),p);sources.append(name);mounts[alias]=name
        else:
            target='/input/refs/'+hashlib.sha256(str(p.parent).encode()).hexdigest()[:20]+'/'+p.name;file_mounts.append({'source':str(p),'target':target})
        bindings[str(p)]=target
    def argument(value):
        for origin,target in sorted(bindings.items(),key=lambda pair:-len(pair[0])):
            if value==origin or value.startswith(origin+'/'):return target+value[len(origin):]
        return value
    job=str(uuid.uuid4());spec={'id':job,'image':image,'argv':[argument(v) for v in argv],'sources':list(dict.fromkeys(sources)),'mounts':mounts,'files':file_mounts,'cpu':body.get('cpu',20),'memoryMb':body.get('memoryMb',51200),'timeoutSeconds':body.get('timeoutSeconds',86400),'context':body.get('context'),'title':body.get('title','工具分析')}
    result=r.dispatch({'action':'submit','body':spec});r.audit(r.database(),'workspace.analysis_submitted',{'id':job,'inputs':inputs,'image':image})
    return {'id':job,'job':result,'state':'queued','output':str(r.jobdir(job)/'output'),'inputMounts':bindings,'writablePath':'/output','originalDataReadOnly':not r.conda_runtime.is_direct(image),'inputPolicy':'preserve-originals'}

def index(r,body):
    kind=body.get('kind','hisat2');recipes={'hisat2':('rnaseq','rnaseq_align','hisat2-build'),'salmon':('rnaseq','rnaseq_quant','salmon'),'star':('singlecell','singlecell_analyze','STAR'),'bowtie2':('rnaseq','rnaseq_align','bowtie2-build'),'bwa':('rnaseq','rnaseq_align','bwa')}
    if kind not in recipes:raise ValueError('请选择 HISAT2、Salmon、STAR、Bowtie2 或 BWA 索引')
    fasta=read_path(r,body.get('fasta'))
    if not fasta.is_file():raise ValueError('参考序列必须是 FASTA 文件')
    db=r.database();cfg=r.config(db);flow,step,tool=recipes[kind];env=cfg.get('workflowEnvironments',{}).get(flow,{})
    image=env.get('stageImages',{}).get(step,env.get('image'))
    if image not in cfg.get('images',[]) or not r.immutable_image(image):raise ValueError('请先在一体化分析流程中检查并按需配置对应步骤的环境')
    def mount(p):return {'source':str(p),'target':'/input/refs/'+hashlib.sha256(str(p.parent).encode()).hexdigest()[:20]+'/'+p.name}
    mounts=[mount(fasta)];mounted=mounts[0]['target']
    cpu=body.get('cpu',20);argv=[]
    if kind=='hisat2':argv=[tool,'-p',str(cpu),mounted,'/output/genome']
    elif kind=='bowtie2':argv=[tool,'--threads',str(cpu),mounted,'/output/genome']
    elif kind=='salmon':argv=[tool,'index','-p',str(cpu),'-t',mounted,'-i','/output/index']
    elif kind=='bwa':argv=[tool,'index','-p','/output/genome',mounted]
    elif kind=='star':
        gtf=read_path(r,body.get('gtf'));mounts.append(mount(gtf))
        argv=[tool,'--runMode','genomeGenerate','--runThreadN',str(cpu),'--genomeDir','/output','--genomeFastaFiles',mounted,'--sjdbGTFfile',mounts[-1]['target']]
    job=str(uuid.uuid4());spec={'id':job,'image':image,'argv':argv,'sources':[],'files':mounts,'cpu':cpu,'memoryMb':body.get('memoryMb',51200),'timeoutSeconds':body.get('timeoutSeconds',86400),'referenceIndex':{'kind':kind,'fasta':str(fasta)},'context':body.get('context'),'title':kind.upper()+' 参考索引'}
    result=r.dispatch({'action':'submit','body':spec})
    prefix=r.jobdir(job)/'output'/('index' if kind=='salmon' else '' if kind=='star' else 'genome')
    r.audit(db,'workspace.index_submitted',{'id':job,'kind':kind,'fasta':str(fasta),'output':str(prefix)})
    return {'job':result,'id':job,'kind':kind,'indexPath':str(prefix),'state':'queued','message':'索引在工作文件夹内生成；完成后可以用于流程参数。'}

def dispatch(r,action,body):
    allowed={'server_files':{'operation','path','offset'},'workspace_files':{'operation','path','offset'},'workspace_config':{'path','format','content','columns'},'workspace_restore':{'trashId'},'workspace_index':{'kind','fasta','gtf','cpu','memoryMb','timeoutSeconds'},'workspace_script':{'path','content'},'workspace_analysis':{'inputs','argv','image','workflow','step','cpu','memoryMb','timeoutSeconds'}}
    allowed['workspace_files'].update({'destination','query'});allowed['workspace_analysis'].add('title')
    if action not in allowed or not isinstance(body,dict) or set(body)-allowed[action]-{'context'}:raise ValueError('文件操作参数不正确')
    if body.get('context'):
        import workspace_context
        body={**body,'context':workspace_context.normalize(body['context'])}
    return {'server_files':files,'workspace_files':manage,'workspace_config':write_config,'workspace_restore':restore,'workspace_index':index,'workspace_script':script,'workspace_analysis':analysis}[action](r,body)
