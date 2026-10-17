"""Detached, observable first-connection setup; reconnecting never restarts completed setup."""
import json,os,shutil,subprocess,sys,time,uuid
from pathlib import Path

def path(r):return r.root()/'setup.json'

def save(r,value):
    target=path(r);temp=target.with_name('setup-'+str(uuid.uuid4())+'.tmp')
    with temp.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False)
    temp.replace(target)

def status(r):
    p=path(r)
    if p.is_symlink():raise ValueError('环境配置记录不能是链接')
    item=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'state':'idle','detail':'尚未准备基础环境'}
    detail=item.get('detail','')
    if item.get('state')=='failed' and detail.startswith('Command ') and 'podman' in detail and 'timed out' in detail:
        item={**item,'detail':'上次环境检查超时。请重新检查，已配置的环境会继续使用。','errorDetail':detail}
    if item['state']=='running':
        alive=True
        if item.get('pid') and os.name=='posix':
            try:alive=('\0--setup\0'+item['id']) in Path('/proc/'+str(item['pid'])+'/cmdline').read_bytes().decode(errors='replace')
            except FileNotFoundError:alive=False
            except PermissionError:pass
        if not alive or time.time()-item.get('updated',0)>1800:
            item={**item,'state':'interrupted','detail':'上次配置未返回结果，请重试检查；已有组件会复用'}
    return item

def progress(r,message):
    if os.environ.get('OMAAX_SETUP')!='1':return
    item=status(r);item.update(state='running',detail=message,updated=time.time());save(r,item)

def environments(r,body):
    if set(body)-{'conda'} or type(body.get('conda',False)) is not bool:raise ValueError('环境查询参数不正确')
    db=r.database();cfg=r.config(db);approved=cfg.get('images',[]);rows=[];error=''
    try:
        if not shutil.which('podman'):raise ValueError('尚未安装服务器运行组件，请先准备基础环境。')
        rows=json.loads(r.command(['podman','images','--no-trunc','--format','json'],timeout=30))
        if not isinstance(rows,list):raise ValueError('服务器返回的环境清单格式不正确')
    except (ValueError,OSError,subprocess.TimeoutExpired) as reason:rows=[];error=str(reason)[-2000:]
    images={};aliases={}
    for original in rows:
        if not isinstance(original,dict):continue
        row={str(k).lower():v for k,v in original.items()}
        identity='sha256:'+str(row.get('id','')).removeprefix('sha256:')
        if not r.immutable_image(identity):continue
        tags=row.get('names') or row.get('repotags') or []
        tags=[tag for tag in tags if isinstance(tag,str)] if isinstance(tags,list) else []
        references={identity,*(row.get('repodigests') or [])}
        if not references.intersection(approved) and not any(tag.startswith(('localhost/omaax-','localhost/biodsh-')) for tag in tags):continue
        size=row.get('size');size=size if isinstance(size,int) and size>=0 else None
        images[identity]={'id':identity,'tags':tags,'size':size,'installed':True,'approved':bool(references.intersection(approved)),'bindings':[],'activeJobs':0}
        for ref in references:aliases[ref]=identity
    for ref in approved:
        if not r.immutable_image(ref):continue
        key=aliases.get(ref,ref)
        images.setdefault(key,{'id':key,'tags':[],'size':None,'installed':False,'approved':True,'bindings':[],'activeJobs':0})
        aliases[ref]=key
    for workflow,entry in cfg.get('workflowEnvironments',{}).items():
        bindings={}
        if entry.get('image'):bindings[entry['image']]={'workflow':workflow,'default':True,'steps':[]}
        for step,ref in entry.get('stageImages',{}).items():bindings.setdefault(ref,{'workflow':workflow,'default':False,'steps':[]})['steps'].append(step)
        for ref,binding in bindings.items():
            key=aliases.get(ref,ref)
            if key in images:images[key]['bindings'].append(binding)
    for job in db.execute("SELECT spec FROM jobs WHERE state IN ('queued','running')"):
        spec=json.loads(job['spec']);refs={spec.get('image'),*(stage.get('image') for stage in spec.get('pipeline',[]))}
        for key in {aliases.get(ref,ref) for ref in refs if ref}:
            if key in images:images[key]['activeJobs']+=1
    result={'images':list(images.values()),'error':error,'setup':status(r),'checkedAt':time.time()}
    if body.get('conda'):result['conda']=conda_environments()
    return result

def conda_environments():
    # Conda's env-list command may rewrite its registry; read metadata directly instead.
    home=Path.home();prefixes=[home/name for name in ('miniconda3','anaconda3','miniforge3','mambaforge')]
    executable=shutil.which('conda')
    if executable:prefixes.append(Path(executable).resolve().parent.parent)
    candidates=list(prefixes);notes=[];record=home/'.conda/environments.txt'
    try:
        if record.is_file():
            if record.stat().st_size>2*1048576:raise ValueError('Conda 登记清单较大，未读取全部记录')
            candidates += [Path(line.strip()).expanduser() for line in record.read_text(encoding='utf-8').splitlines() if line.strip()]
    except (OSError,ValueError) as reason:notes.append(str(reason))
    folders=[home/'.conda/envs',*[p/'envs' for p in prefixes],*[Path(p).expanduser() for p in os.environ.get('CONDA_ENVS_PATH','').split(os.pathsep) if p]]
    for folder in dict.fromkeys(folders):
        try:
            if not folder.is_dir():continue
            with os.scandir(folder) as entries:
                for index,entry in enumerate(entries):
                    if index>=2000:notes.append('部分环境目录超过 2000 项，请缩小查询范围');break
                    if entry.is_dir():candidates.append(Path(entry.path))
        except OSError as reason:notes.append(str(reason))
    found=[]
    for prefix in dict.fromkeys(candidates):
        try:
            if prefix.is_absolute() and (prefix/'conda-meta/history').is_file():found.append({'name':prefix.name,'path':str(prefix)})
        except OSError as reason:notes.append(str(reason))
    return {'environments':sorted(found,key=lambda row:row['path']),'error':'；'.join(dict.fromkeys(notes))[-2000:]}

def probe_image(r,approved):
    """Reuse the smallest approved local image, not the first large analysis environment."""
    if not approved:return None
    rows=json.loads(r.command(['podman','images','--no-trunc','--format','json'],timeout=30))
    candidates=[]
    for row in rows:
        identity=str(row.get('Id','')).removeprefix('sha256:')
        references={identity,'sha256:'+identity,*(row.get('RepoDigests') or [])}
        for index,image in enumerate(approved):
            if image in references:candidates.append((int(row.get('Size',0)),index,image))
    return min(candidates)[2] if candidates else approved[0]

class ProbeFailure(ValueError):
    def __init__(self,message,diagnostics):
        super().__init__(message);self.diagnostics=diagnostics

def verify_environment(r,image):
    token=uuid.uuid4().hex;name='omaax-envcheck-'+token
    # The same rootless, read-only, no-network constraints apply as in actual analysis jobs.
    args=['podman','run','--rm','--name',name,'--label','org.omaax.environment-check='+token,'--pull=never',
          '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--userns=keep-id',
          '--cpus=1','--memory=512m','--memory-swap=512m','--entrypoint','/bin/sh',image,
          '-c','printf "omaax-isolation-ready\\n"']
    progress(r,'正在启动基础环境自检；首次准备用户映射可能需要几分钟，SSH 连接不受影响')
    try:
        output=r.command(args,timeout=180)
        if output.strip()!='omaax-isolation-ready':raise ValueError('环境没有返回预期的自检结果')
    except (subprocess.TimeoutExpired,ValueError) as error:
        diagnostics=str(error)[-4000:]
        record={'image':image,'container':name,'error':diagnostics,'created':time.time()}
        (r.root()/('environment-probe-'+token+'.json')).write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
        try:
            item=json.loads(r.command(['podman','container','inspect',name],timeout=8))[0]
            if item.get('Config',{}).get('Labels',{}).get('org.omaax.environment-check')==token:
                r.command(['podman','rm','--force','--time','2',name],timeout=15)
        except (ValueError,subprocess.TimeoutExpired,IndexError,KeyError):pass
        message='基础环境启动验证超时（180 秒），SSH 登录正常。请重试基础环境检查；首次镜像映射较慢，已有分析环境不会被删除。' if isinstance(error,subprocess.TimeoutExpired) else '基础环境未通过启动验证，SSH 登录正常。请展开技术详情查看原因后重试。'
        raise ProbeFailure(message,diagnostics) from error

def run(r,token):
    import fcntl
    with (r.root()/'setup.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        item=status(r)
        if item.get('id')!=token:return
        item.update(pid=os.getpid(),updated=time.time());save(r,item)
        os.environ['OMAAX_SETUP']='1'
        try:
            r.dispatch({'action':'bootstrap','body':item.get('request',{})})
            item.update(state='complete',detail='基础环境已就绪；分析工具在使用时按需配置')
        except Exception as error:
            if isinstance(error,subprocess.TimeoutExpired):
                item.update(state='failed',detail='基础环境配置超时，SSH 登录正常；请重试或查看技术详情。',errorDetail=str(error)[-2000:])
            else:item.update(state='failed',detail=str(error)[-2000:],errorDetail=getattr(error,'diagnostics',''))
        item['updated']=time.time();save(r,item)

def dispatch(r,action,body):
    if action=='server_environments':return environments(r,body)
    import server_workspace as storage
    work=storage.workspace(r)
    if action=='initialize':
        return {'workspace':str(work),'setup':status(r),'connected':True}
    if action=='setup_status':return status(r)
    import fcntl
    if set(body)-{'retry','image'}:raise ValueError('环境准备参数不正确')
    with (r.root()/'setup-start.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        item=status(r)
        if item['state']=='running' or item['state']=='complete' and not body.get('retry') and shutil.which('podman') and 0<=time.time()-item.get('updated',0)<21600:return item
        token=str(uuid.uuid4());item={'id':token,'state':'running','detail':'正在准备基础环境，可以切换页面','updated':time.time(),'request':{'image':body['image']} if body.get('image') else {}}
        save(r,item)
        try:subprocess.Popen([sys.executable,r.__file__,'--setup',token],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        except Exception as error:
            item.update(state='failed',detail=str(error));save(r,item);raise
        return item
