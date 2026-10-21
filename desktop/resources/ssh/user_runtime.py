"""Discover working user-level installers without modifying existing site environments."""
import hashlib,json,os,shutil,subprocess,tempfile,sys
from pathlib import Path

BOOTSTRAP_SOURCE = None  # Injected into the frozen runner; standalone tests load the bundled shell file.

def prefix():return Path.home()/'omaax'/'.runtime'/'env'

def safe_directory(path):
    if any(p.is_symlink() for p in [path,*path.parents]):raise ValueError('环境目录不能是符号链接')
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.stat().st_uid!=os.getuid():raise ValueError('环境目录不属于当前服务器账号')
    return path

def activate():
    env=prefix()
    if (env/'bin').is_dir():os.environ['PATH']=str(env/'bin')+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')

def manager():
    """Reuse a site installation read-only; new environments always use private prefixes."""
    candidates=[('micromamba',str(prefix().parent/'micromamba'))]
    for name in ('micromamba','conda'):
        executable=shutil.which(name)
        if executable:candidates.append((name,executable))
    candidates += [('conda',os.environ.get('CONDA_EXE','')),('conda',str(Path(sys.executable).parent/'conda'))]
    for base in (Path.home()/'miniforge3',Path.home()/'miniconda3',Path.home()/'anaconda3',
                 Path.home().parent.parent/'apps/Anaconda3',Path('/opt/conda'),Path('/opt/anaconda3')):
        candidates.append(('conda',str(base/'bin/conda')))
    for kind,candidate in dict.fromkeys(candidates):
        if candidate and Path(candidate).is_file() and os.access(candidate,os.X_OK):
            try:
                env={k:v for k,v in os.environ.items() if k not in ('PYTHONHOME','PYTHONPATH')}
                probe=subprocess.run([candidate,'--version'],env=env,capture_output=True,text=True,timeout=10)
                if probe.returncode==0:return kind,candidate
            except (OSError,subprocess.TimeoutExpired):
                pass
    return None

def package_caches():
    found=manager()
    cache=Path(found[1]).resolve().parent.parent/'pkgs' if found else None
    return [str(cache)] if cache and cache.is_dir() and os.access(cache,os.R_OK|os.X_OK) else []

def cached_explicit(packages):
    """Resolve a compatible installed dependency closure without network repodata."""
    found=manager()
    if not found or found[0]!='conda':return None
    base=Path(found[1]).resolve().parent.parent
    python=base/'bin/python'
    if not python.is_file():return None
    # Use Conda's own version/build matcher, not a partial home-grown solver.
    script=r'''
import hashlib,json,pathlib,sys
from conda.models.match_spec import MatchSpec
base=pathlib.Path(sys.argv[1])
records={p.stem:json.loads(p.read_text()) for p in (base/'conda-meta').glob('*.json')}
byname={r['name']:r for r in records.values()}
pending=json.loads(sys.argv[2]);selected={}
while pending:
    spec=MatchSpec(pending.pop())
    if spec.name.startswith('__'):continue
    record=byname.get(spec.name)
    if not record or not spec.match(record):sys.exit(3)
    if spec.name in selected:continue
    selected[spec.name]=record;pending.extend(record.get('depends',[]))
urls=[]
for record in selected.values():
    filename=record.get('fn','')
    if pathlib.Path(filename).name!=filename:sys.exit(3)
    archive=base/'pkgs'/filename
    expected=record.get('sha256') or record.get('md5')
    if not expected or not archive.is_file():sys.exit(3)
    digest=hashlib.new('sha256' if record.get('sha256') else 'md5')
    with archive.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1048576),b''):digest.update(chunk)
    if digest.hexdigest()!=expected:sys.exit(3)
    urls.append(archive.as_uri()+('#'+record['md5'] if record.get('md5') else ''))
print(json.dumps(urls))
'''
    try:
        result=subprocess.run([str(python),'-c',script,str(base),json.dumps(packages)],capture_output=True,text=True,timeout=90)
        return json.loads(result.stdout) if result.returncode==0 else None
    except (OSError,ValueError,subprocess.TimeoutExpired):return None

def install():
    activate()
    if manager():return
    source=BOOTSTRAP_SOURCE or Path(__file__).with_suffix('.sh').read_text(encoding='utf-8')
    process=subprocess.run(['/bin/sh','-c',source+'\nomaax_install_runtime analysis\n'],capture_output=True,text=True,timeout=840)
    if process.returncode:raise ValueError('用户环境安装未完成：'+(process.stderr or process.stdout)[-2000:])
    activate()
    if not manager():raise ValueError('用户环境缺少运行组件，请重新检查配置')
