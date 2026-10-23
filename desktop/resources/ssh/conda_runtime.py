"""Account-owned host and Conda execution; registered paths use software protection."""
import hashlib,io,json,os,re,shutil,subprocess,sys,tokenize,uuid
from pathlib import Path
import user_runtime

HOST_REF='host:direct'

def is_direct(value):
    return value==HOST_REF or is_ref(value)

def is_ref(value):
    return isinstance(value,str) and re.fullmatch(r'conda:[a-f0-9]{64}',value) is not None

def directory(r,ref):
    if not is_ref(ref):raise ValueError('Conda 环境标识无效')
    path=r.root()/'conda-environments'/ref[6:]
    if any(p.is_symlink() for p in [path,*path.parents]):raise ValueError('环境目录不能是链接')
    return path

def resolve(r,ref):
    path=directory(r,ref)
    record=json.loads((path/'ready.json').read_text())
    if record.get('execution')!='direct-v1' or record.get('id')!=ref or not (path/'opt/conda/conda-meta/history').is_file():raise ValueError('环境尚未准备完成，请重新配置')
    return path

def mapping(opt,output=None,mounts=()):
    pairs={str(target):str(source) for source,target in mounts}
    if opt:pairs['/opt']=str(opt)
    if output:pairs['/output']=str(output)
    return pairs

def translate(value,pairs):
    # One pass prevents a replacement path from being translated a second time.
    if not pairs:return value
    pattern='|'.join(re.escape(k) for k in sorted(pairs,key=len,reverse=True))
    return re.sub('(?:'+pattern+r')(?=/|[^A-Za-z0-9_.-]|$)',lambda m:pairs[m.group()],value)

def script_argument(argv):
    name=Path(argv[0]).name.lower().removesuffix('.exe')
    python=re.fullmatch(r'python(?:[23](?:\.\d+)?)?',name) is not None
    shell=name in ('bash','sh','dash','zsh','ksh')
    if not (python or shell or name in ('rscript','node','nodejs','perl','ruby')):return None
    values=({'-W','-X','--check-hash-based-pycs'} if python else {'-o','-O','--rcfile','--init-file'} if shell else {'--encoding'} if name=='rscript' else {'-r','--require','--loader','--import','-I'})
    inline=({'-c','-m'} if python else {'-c','-s'} if shell else {'-e','-E','--eval','-p','--print'})
    i=1
    while i<len(argv):
        value=argv[i]
        if value=='--':return i+1 if i+1<len(argv) else None
        if value in inline or (shell and value.startswith('-') and not value.startswith('--') and any(c in value[1:] for c in ('c','s'))):return None
        if not value.startswith('-'):return i
        i+=2 if value in values else 1
    return None

def command(opt,argv,output=None,mounts=()):
    pairs=mapping(opt,output,mounts);env=dict(os.environ)
    cache=Path(output).parent/'runtime-cache' if output else (opt/'cache' if opt else Path.cwd()/'runtime-cache')
    cache.mkdir(parents=True,exist_ok=True)
    if opt:
        runtime=user_runtime.prefix();prefix=opt/'conda'
        env.update(PATH=str(prefix/'bin')+os.pathsep+str(runtime/'bin')+os.pathsep+os.environ.get('PATH','/usr/bin:/bin'),CONDA_PREFIX=str(prefix),
            MAMBA_ROOT_PREFIX=str(opt/'mamba'),CONDA_PKGS_DIRS=str(opt/'pkgs'),PYTHONDONTWRITEBYTECODE='1',
            OMAAX_NEXTFLOW_ENVS=str(opt/'omaax/nextflow-environments.json'),NXF_HOME=str(opt/'omaax/nxf'),
            OMAAX_PATH_MAP=json.dumps(pairs),OMAAX_OUTPUT=str(output or cache),OMAAX_OPT=str(opt),TMPDIR=str(cache))
        env['CONDA_PKGS_DIRS']=os.pathsep.join([str(opt/'pkgs'),*user_runtime.package_caches()])
        manager=user_runtime.manager()
        if manager:env['OMAAX_CONDA_EXE']=manager[1]
        for key in ('PYTHONHOME','PYTHONPATH'):env.pop(key,None)
    translated=[translate(value,pairs) for value in argv]
    # Adapt only the interpreter's script, regardless of suffix; data arguments stay byte-exact.
    i=script_argument(translated) if output else None
    if i is not None:
        script=Path(translated[i])
        if script.is_file() and script.suffix.lower() not in ('.pyc','.pyo'):
            raw=script.read_bytes();encoding='utf-8'
            python=re.fullmatch(r'python(?:[23](?:\.\d+)?)?(?:\.exe)?',Path(translated[0]).name.lower()) is not None
            if python:encoding=tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
            elif Path(translated[0]).name.lower().removesuffix('.exe')=='rscript':
                for index,value in enumerate(translated[1:i],1):
                    if value=='--encoding' and index+1<i:encoding=translated[index+1]
                    elif value.startswith('--encoding='):encoding=value.split('=',1)[1]
            text=raw.decode(encoding);updated=translate(text,pairs)
            if updated!=text:
                copy=cache/(hashlib.sha256(str(script).encode()).hexdigest()[:16]+script.suffix)
                copy.write_bytes(updated.encode(encoding));translated[i]=str(copy)
                if python:
                    env['PYTHONPATH']=os.pathsep.join(filter(None,[str(script.parent),env.get('PYTHONPATH')]))
    executable=shutil.which(translated[0],path=env['PATH'])
    if not executable:raise ValueError('分析环境缺少程序：'+translated[0]+'；请配置依赖后重试')
    translated[0]=executable
    return translated,env,str(output or cache)

def protection():
    uid=os.getuid()
    return {'backend':'conda','readonlyInputs':False,'inputPolicy':'registered-paths','deletionApproval':'registered-paths-only','uid':uid,'rootless':uid!=0,'isolation':False,'cgroup':'not-required'}

def args(r,ref,argv,output=None,mounts=()):
    cmd,env,cwd=command(None if ref==HOST_REF else resolve(r,ref)/'opt',argv,output,mounts)
    return ['/usr/bin/env','-u','PYTHONHOME','-u','PYTHONPATH',*[key+'='+value for key,value in env.items() if os.environ.get(key)!=value or key in ('PYTHONHOME','PYTHONPATH')],*cmd]

def network_run(opt,argv,log,output=None,mounts=(),writable=True):
    cmd,env,cwd=command(opt,argv,output,mounts)
    return subprocess.call(cmd,env=env,cwd=cwd,stdout=log,stderr=log)

def create(r,packages,channels,log,context=None):
    """Publish a fresh environment only after install and its verification succeed."""
    ref='conda:'+hashlib.sha256(uuid.uuid4().bytes).hexdigest();path=directory(r,ref)
    opt=path/'opt';opt.mkdir(parents=True,mode=0o700);(opt/'omaax').mkdir()
    if context:
        for name in ('upstream','prepare-nextflow.py','model-install.sh'):
            source=context/name
            if source.is_dir():shutil.copytree(source,opt/'omaax'/name)
            elif source.is_file():(opt/'omaax'/name).write_text(translate(source.read_text(),{'/opt':str(opt)}))
    manager=user_runtime.manager()
    if not manager:
        log.write('未找到可运行的 Conda，正在准备独立用户安装器；已有环境不修改。\n'.encode());log.flush()
        user_runtime.install()
        manager=user_runtime.manager()
    if not manager:raise ValueError('用户安装器准备未完成，请检查环境安装日志')
    kind,executable=manager
    base=[executable,*(['--no-rc'] if kind=='micromamba' else [])]
    install_command=[*base,'create','--yes','--prefix','/opt/conda','--override-channels',*[v for c in channels for v in ('-c',c)],*packages]
    # Resolve against cached packages before requesting the network. A dry run
    # cannot publish a half-installed environment or modify the shared base.
    explicit=user_runtime.cached_explicit(packages)
    if explicit:
        manifest=path/'cached-explicit.txt'
        manifest.write_text('@EXPLICIT\n'+'\n'.join(explicit)+'\n',encoding='utf-8')
        install_command=[*base,'create','--yes','--prefix','/opt/conda','--offline','--file',str(manifest)]
    else:
        offline=install_command+['--offline']
        if network_run(opt,offline+['--dry-run'],log)==0:
            install_command=offline
    commands=[install_command]
    if (opt/'omaax/model-install.sh').exists():commands.append(['/bin/bash','-eu','/opt/omaax/model-install.sh'])
    if (opt/'omaax/prepare-nextflow.py').exists():commands.append(['python3','/opt/omaax/prepare-nextflow.py'])
    commands.append(['python3','-c','import ssl,sqlite3,ctypes,yaml'])
    for install_command in commands:
        result=network_run(opt,install_command,log)
        if result:raise ValueError('Conda 环境安装失败，原有环境未改变；请查看日志并修正安装方案')
    cmd,env,cwd=command(opt,[executable,'list','-p','/opt/conda','--explicit'])
    lock=subprocess.run(cmd,env=env,cwd=cwd,capture_output=True,text=True,timeout=60)
    if lock.returncode:raise ValueError('环境依赖记录失败')
    (path/'explicit.txt').write_text(lock.stdout)
    (path/'ready.json').write_text(json.dumps({'id':ref,'execution':'direct-v1','packages':packages,'channels':channels,'lockSha256':hashlib.sha256(lock.stdout.encode()).hexdigest()}))
    return ref

