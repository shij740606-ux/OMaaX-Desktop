"""User-owned, SSH-transported task runner. No listening port, sudo or platform account."""
import base64
import contextlib
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import selectors
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import types
import user_runtime
import conda_runtime
import scheduler_backend
user_runtime.activate()

_machine = Path('/etc/machine-id')
_identity = (_machine.read_text().strip() if _machine.exists() else '') + ':' + platform.node()
# Shared NFS home directories must not merge two servers' queues and execution locks.
SERVER_KEY = hashlib.sha256(_identity.encode()).hexdigest()[:16]
if os.environ.get('OMAAX_SERVER_KEY'):
    if not re.fullmatch(r'[a-f0-9]{16}',os.environ['OMAAX_SERVER_KEY']):raise ValueError('服务器标识无效')
    if not scheduler_backend.allocation():raise ValueError('未核实计算节点分配')
    SERVER_KEY=os.environ['OMAAX_SERVER_KEY']
APP_HOME = Path.home() / 'omaax'
LEGACY_ROOT = Path.home() / '.local/share/biodsh-personal' / SERVER_KEY
DEFAULT_ROOT = APP_HOME / '.servers' / SERVER_KEY
# Keep existing queues in place, including supervisors started by older versions.
ROOT = LEGACY_ROOT if (LEGACY_ROOT/'state.sqlite').is_file() else DEFAULT_ROOT
VERSION = 1


def immutable_image(value):
    return conda_runtime.is_ref(value) or isinstance(value, str) and re.fullmatch(r'(?:[^\s]+@)?sha256:[a-f0-9]{64}', value)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
        raise ValueError('Invalid identifier')
    return value


def root():
    # A remote account can change its own files; never trust a redirected control directory.
    for part in [ROOT, *ROOT.parents]:
        if part.is_symlink():
            raise ValueError('Control directory must not contain symlinks')
    if ROOT==DEFAULT_ROOT:
        APP_HOME.mkdir(mode=0o700,exist_ok=True)
        if APP_HOME.stat().st_uid!=os.getuid():raise ValueError('OMaaX 文件夹不属于当前账号')
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ROOT.stat().st_uid != os.getuid():
        raise ValueError('Control directory owner mismatch')
    os.chmod(ROOT, 0o700)
    if ROOT == DEFAULT_ROOT:
        # These overrides affect only OMaaX child processes, never the user's global container setup.
        for name in ('cache','tmp','container-storage','container-run'):
            p=ROOT/name
            if p.is_symlink():raise ValueError('应用运行目录不能是链接')
            p.mkdir(mode=0o700,exist_ok=True)
        storage=ROOT/'storage.conf'
        if storage.is_symlink():raise ValueError('容器配置不能是链接')
        import fcntl
        with (ROOT/'storage.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if not storage.exists():
                with storage.open('x') as f:f.write('[storage]\ndriver = "overlay"\ngraphroot = '+json.dumps(str(ROOT/'container-storage'))+'\nrunroot = '+json.dumps(str(ROOT/'container-run'))+'\n')
        os.environ['CONTAINERS_STORAGE_CONF']=str(storage)
        os.environ['XDG_CACHE_HOME']=str(ROOT/'cache')
        os.environ['TMPDIR']=str(ROOT/'tmp')
        user_runtime.activate()
    return ROOT


def workspace_root():
    return APP_HOME/'workspace' if ROOT in (DEFAULT_ROOT, LEGACY_ROOT) else root()/'workspace'


def database():
    db = sqlite3.connect(root() / 'state.sqlite', timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL, created REAL, updated REAL, detail TEXT);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at REAL, action TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS workspace_contexts(project_id TEXT,session_id TEXT,metadata TEXT,path TEXT,updated REAL,PRIMARY KEY(project_id,session_id));
        CREATE TABLE IF NOT EXISTS job_locations(job_id TEXT PRIMARY KEY,path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_external_files(path TEXT PRIMARY KEY,context TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS transfer_imports(id TEXT PRIMARY KEY,path TEXT NOT NULL);
    ''')
    return db


def audit(db, action, detail):
    db.execute('INSERT INTO audit(at,action,detail) VALUES(?,?,?)', (time.time(), action, json.dumps(detail)))
    db.commit()


def config(db):
    return {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM config')}


def command(args, timeout=20):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise ValueError((p.stderr or p.stdout or 'Command failed')[-2000:])
    return p.stdout


def protection():
    return conda_runtime.protection()


def image_command(image,argv):
    if conda_runtime.is_ref(image):return conda_runtime.args(types.SimpleNamespace(**globals()),image,argv)
    return ['podman','run','--rm','-i','--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--user=0:0','--cpus=1','--memory=2048m',image,*argv]


def source_path(value, imported=False):
    p = Path(value).expanduser()
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError('Select an absolute, non-symlink data directory')
    p = p.resolve(strict=True)
    managed_import = (imported and p.parent == ROOT/'imports' and re.fullmatch(r'[a-f0-9-]{36}', p.name)) or p == ROOT/'shared'
    managed_workspace = p == workspace_root() or workspace_root() in p.parents
    denied = [*([] if managed_import or managed_workspace else [ROOT]), Path('/proc'), Path('/sys'), Path('/dev'), Path.home()/'.ssh', Path.home()/'.aws', Path.home()/'.kube']
    if not p.is_dir() or any(p == d or (d != Path('/') and d != Path.home() and (d in p.parents or p in d.parents)) for d in denied):
        raise ValueError('System, home root, credentials and control directories cannot be shared')
    if not os.access(p, os.R_OK | os.X_OK):
        raise ValueError('SSH account cannot read this directory')
    mounts = Path('/proc/self/mountinfo')
    if mounts.exists():
        for row in mounts.read_text().splitlines():
            mounted = Path(row.split()[4].replace('\\040',' ').replace('\\134','\\'))
            if p in mounted.parents:
                raise ValueError('Register nested mount points separately; recursive read-only cannot be assumed')
    return p


def contained(base, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Invalid relative path')
    p = base / relative
    if any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError('Symlinks are not transferable')
    p = p.resolve(strict=True)
    if p != base and base not in p.parents:
        raise ValueError('Path escapes selected data root')
    return p


def memory():
    values = dict((line.split(':')[0], int(line.split()[1])*1024) for line in Path('/proc/meminfo').read_text().splitlines())
    return values['MemAvailable']


def disk_available():
    usage = shutil.disk_usage(root())
    return max(0, usage.free-max(1073741824, usage.total//100))


def validate_job(spec, cfg):
    identifier(spec.get('id'))
    image = spec.get('image')
    if image not in cfg.get('images', []) or not immutable_image(image):
        raise ValueError('Image must be an approved immutable sha256 image')
    argv = spec.get('argv')
    if not isinstance(argv, list) or not 1 <= len(argv) <= 128 or any(not isinstance(a, str) or '\x00' in a or len(a)>8192 for a in argv):
        raise ValueError('Invalid container argument list')
    for key, low, high in [('cpu', 1, os.cpu_count() or 1), ('memoryMb', 128, 1048576), ('timeoutSeconds', 10, 604800)]:
        if type(spec.get(key)) is not int or not low <= spec[key] <= high:
            raise ValueError('Invalid '+key)
    if spec.get('gpu', 0):
        raise ValueError('GPU isolation is not enabled; refusing unprotected GPU execution')
    sources = spec.get('sources', [])
    if not isinstance(sources, list) or len(sources)>32 or len(sources)!=len(set(sources)):
        raise ValueError('Invalid source list')
    for name in sources:
        identifier(name)
        if name not in cfg.get('sources', {}):
            raise ValueError('Source is not registered on this server: '+name)
        source_path(cfg['sources'][name], imported=name.startswith('import_'))
    mounts = spec.get('mounts', {name:name for name in sources})
    if not isinstance(mounts, dict) or set(mounts.values()) != set(sources):
        raise ValueError('Invalid source mount mapping')
    for alias in mounts: identifier(alias)
    files=spec.get('files',[])
    if not isinstance(files,list) or len(files)>256:raise ValueError('参考文件数量过多')
    if files:
        import server_workspace
        for file in files:
            p=server_workspace.read_path(types.SimpleNamespace(**globals()),file.get('source'))
            if file.get('inputDirectory'):
                if 'workflow' not in spec or not p.is_dir() or str(p)!=file['source'] or file.get('target')!='/input/linked/'+hashlib.sha256(str(p).encode()).hexdigest()[:20]:raise ValueError('输入链接目录挂载不正确')
                continue
            if not p.is_file() or not isinstance(file.get('target'),str) or any(c in file['target'] for c in ('\0','\n','\r',':','\\')) or not re.fullmatch(r'/input/refs/[a-f0-9]{20}/.+',file['target']) or any(x in ('.','..','') for x in file['target'].split('/')[2:]):raise ValueError('参考文件挂载不正确')
    if 'pipeline' in spec and 'workflow' not in spec:
        raise ValueError('Only fixed workflows may select per-step runtime images')
    for stage in spec.get('pipeline',[]):
        if stage.get('image') not in cfg.get('images',[]) or not immutable_image(stage.get('image')):
            raise ValueError('Step image must be approved by the user')
    if 'workflow' in spec:
        import workflow_engine
        workflow_engine.validate_bound_job(types.SimpleNamespace(**globals()), spec)
    return spec


def jobdir(job,spec=None):
    legacy=root()/'jobs'/identifier(job)
    if legacy.exists():path=legacy
    else:
        import server_workspace
        row=database().execute('SELECT path FROM job_locations WHERE job_id=?',(identifier(job),)).fetchone()
        if row:
            saved=Path(row['path']);old=root()/'workspace'/saved
            path=old if not saved.is_absolute() and old.exists() else server_workspace.managed(types.SimpleNamespace(**globals()),row['path'],existing=False)
        else:
            old=server_workspace.workspace(types.SimpleNamespace(**globals()))/'results'/identifier(job)
            if old.exists():path=old
            else:
                if spec is None:
                    saved=database().execute('SELECT spec FROM jobs WHERE id=?',(job,)).fetchone();spec=json.loads(saved['spec']) if saved else {'id':job}
                import workspace_context
                path=workspace_context.taskdir(types.SimpleNamespace(**globals()),spec)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Invalid job directory')
    return path


def update(db, job, state, detail=''):
    db.execute('UPDATE jobs SET state=?, updated=?, detail=? WHERE id=?', (state, time.time(), detail, job))
    audit(db, 'job.'+state, {'id': job, 'detail': detail})


def container_args(spec, cfg):
    out = jobdir(spec['id']) / 'output'
    if conda_runtime.is_ref(spec['image']):
        mounts=[(str(source_path(cfg['sources'][name],imported=name.startswith('import_'))),'/input/'+alias) for alias,name in spec.get('mounts',{n:n for n in spec.get('sources',[])}).items()]
        mounts += [(v['source'],v['target']) for v in spec.get('files',[])]
        payload={'opt':str(conda_runtime.resolve(types.SimpleNamespace(**globals()),spec['image'])/'opt'),'argv':spec['argv'],'output':str(out),'mounts':mounts,'cpu':spec['cpu'],'memoryMb':spec['memoryMb']}
        return [sys.executable,str(Path(__file__).resolve()),'--conda-exec',json.dumps(payload)]
    args = ['podman', 'run', '--name', 'biodsh-'+spec['id'], '--label', 'biodsh.personal='+spec['id'],
            '--pull=never', '--network=slirp4netns:allow_host_loopback=false', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user=0:0', '--pids-limit='+str(max(512,min(4096,256+spec['cpu']*32))), '--cpus='+str(spec['cpu']), '--memory='+str(spec['memoryMb'])+'m',
            '--memory-swap='+str(spec['memoryMb'])+'m', '--env=HOME=/tmp', '--env=XDG_CACHE_HOME=/tmp/.cache', '--env=TMPDIR=/tmp',
            '--volume', str(out)+':/output:rw', '--workdir=/output']
    # The private container layer permits package caches and large disk-backed temporary files.
    # Only this job's output is host-writable; original inputs stay read-only.
    for alias, name in spec.get('mounts', {name:name for name in spec.get('sources', [])}).items():
        path = str(source_path(cfg['sources'][name], imported=name.startswith('import_')))
        if ':' in path or '\n' in path:
            raise ValueError('Unsupported mount path')
        args += ['--volume', path+':/input/'+alias+':ro']
    for file in spec.get('files',[]):
        path=file['source'];target=file['target']
        if ':' in path or '\n' in path or ':' in target or '\n' in target:raise ValueError('Unsupported reference mount path')
        args+=['--volume',path+':'+target+':ro']
    return args + [spec['image']] + spec['argv']


def task_process_alive(directory):
    marker=directory/'process.json'
    if not marker.exists():return False
    saved=json.loads(marker.read_text());pid=saved['pid']
    try:
        stat=Path('/proc/'+str(pid)+'/stat').read_text().split(') ')[1].split()
        return stat[0]!='Z' and stat[19]==saved['start'] and b'--conda-exec\0' in Path('/proc/'+str(pid)+'/cmdline').read_bytes()
    except FileNotFoundError:return False


def cancel_container(job):
    directory=jobdir(identifier(job));marker=directory/'process.json'
    if marker.exists():
        import signal
        saved=json.loads(marker.read_text());pid=saved['pid']
        try:
            stat=Path('/proc/'+str(pid)+'/stat').read_text().split(') ')[1].split()
            if stat[19]!=saved['start']:return False
            if b'--conda-exec\0' not in Path('/proc/'+str(pid)+'/cmdline').read_bytes():raise ValueError('任务进程身份不符')
            os.killpg(pid,signal.SIGKILL);return True
        except ProcessLookupError:return False
        except FileNotFoundError:return False
    name = 'biodsh-'+identifier(job)
    try:
        label = command(['podman', 'inspect', '--format', '{{ index .Config.Labels "biodsh.personal" }}', name]).strip()
    except (ValueError, FileNotFoundError):
        return False
    if label != job:
        raise ValueError('Container ownership label mismatch')
    command(['podman', 'stop', '--time', '5', name])
    return True


def append_task_log(log,chunk,limit=10*1024*1024):
    """Rotate complete segments instead of silently dropping long-task output."""
    while chunk:
        room=limit-log.tell()
        if room<=0:
            path=Path(log.name)
            log.flush()
            archive=path.with_name('log-'+str(time.time_ns())+'-'+uuid.uuid4().hex[:8]+'.txt')
            # Copy before truncating the open stream so existing callers retain
            # the same handle and a failed copy never discards the original.
            with archive.open('xb') as output,path.open('rb') as source:
                shutil.copyfileobj(source,output)
                output.flush();os.fsync(output.fileno())
            log.seek(0);log.truncate(0);room=limit
        part,chunk=chunk[:room],chunk[room:]
        log.write(part);log.flush()

def execute_container(job,spec,cfg,directory,started,log):
    args=container_args(spec,cfg)
    if 'workflow' in spec:args.insert(2,'--rm')
    if conda_runtime.is_ref(spec['image']) and 'workflow' in spec:args.remove('--rm')
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    if conda_runtime.is_ref(spec['image']):
        (directory/'process.json').write_text(json.dumps({'pid':process.pid,'start':Path('/proc/'+str(process.pid)+'/stat').read_text().split(') ')[1].split()[19]}))
    poller=selectors.DefaultSelector();poller.register(process.stdout,selectors.EVENT_READ)
    stopping=False
    while process.poll() is None:
        if not stopping and ((directory/'cancel').exists() or time.time()-started>spec['timeoutSeconds'] or disk_available()==0):
            stopping=cancel_container(job)
        for key,_ in poller.select(0.5):
            chunk=os.read(key.fileobj.fileno(),65536)
            if not chunk:poller.unregister(key.fileobj)
            append_task_log(log,chunk)
    if conda_runtime.is_ref(spec['image']):
        import signal
        # Finish the owned process group so background children cannot hold logs open.
        with contextlib.suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGKILL)
    while chunk:=process.stdout.read(65536):
        append_task_log(log,chunk)
    process.stdout.close();poller.close()
    return process.returncode,stopping


def admission(db, spec):
    if os.environ.get('OMAAX_SERVER_KEY'):
        # Slurm already reserves per-job CPU/memory; unrelated nodes must not
        # serialize each other through the shared home database.
        return True
    running=[json.loads(row['spec']) for row in db.execute("SELECT spec FROM jobs WHERE state='running'")]
    total_memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))//1024
    return (len(running)<4 and sum(s['cpu'] for s in running)+spec['cpu'] <= (os.cpu_count() or 1)
            and sum(s['memoryMb'] for s in running)+spec['memoryMb'] <= total_memory
            and memory() >= spec['memoryMb']*1048576)


def tree_manifest(directory,native_root=None):
    rows=[]
    for file in sorted(directory.rglob('*')):
        relative=file.relative_to(directory).as_posix()
        if file.is_symlink():
            link=os.readlink(file)
            if link.startswith('/output/'):
                resolved=(directory/link.removeprefix('/output/')).resolve()
            elif native_root is not None and Path(link).is_absolute() and Path(link).is_relative_to(native_root):
                # Native Conda paths in an older snapshot still name the task's
                # original output. Validate the equivalent target in the snapshot.
                resolved=(directory/Path(link).relative_to(native_root)).resolve()
            else:
                resolved=(file.parent/link).resolve()
            if directory.resolve() not in resolved.parents:raise ValueError('续跑快照不能包含外部链接')
            rows.append({'path':relative,'link':link})
        elif file.is_file():
            digest=hashlib.sha256()
            with file.open('rb') as handle:
                while chunk:=handle.read(1048576):digest.update(chunk)
            rows.append({'path':relative,'size':file.stat().st_size,'sha256':digest.hexdigest()})
        elif not file.is_dir():raise ValueError('续跑快照不能包含特殊文件')
    return rows


def checkpoint(directory,spec,completed):
    # Snapshot outside the writable task mount. Never trust a manifest written by a model.
    out=directory/'output';manifest=tree_manifest(out)
    if sum(row.get('size',0) for row in manifest)>disk_available():raise ValueError('空间不足，无法保存续跑快照；已有结果保留')
    target=directory/('checkpoint-'+str(uuid.uuid4()));target.mkdir(mode=0o700)
    shutil.copytree(out,target/'output',symlinks=True)
    # Absolute internal symlinks resolve against /output in the container, not the snapshot.
    data={'spec':spec,'completed':list(completed),'files':manifest,'directory':target.name}
    temp=directory/'checkpoint.tmp';temp.write_text(json.dumps(data),encoding='utf-8')
    os.replace(temp,directory/'checkpoint.json')


def read_resume(directory,spec):
    marker=directory/'resume.json'
    if not marker.exists():return []
    data=json.loads(marker.read_text())
    steps=[s['step'] for s in spec.get('pipeline',[])]
    completed=data.get('completed',[])
    if data.get('spec')!=spec or not completed or completed!=steps[:len(completed)]:raise ValueError('续跑记录与确认计划不一致')
    return list(completed)


def resume_job(job):
    import fcntl
    job=identifier(job);directory=jobdir(job);db=database()
    with (directory/'supervisor.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('任务仍在运行，请等待停止后续跑')
        row=db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()
        if not row or row['state'] not in ('failed','cancelled','interrupted','invalid_result'):raise ValueError('只有已停止的失败或中断任务可以续跑')
        scheduler=directory/'scheduler.json'
        if scheduler.exists():
            record=json.loads(scheduler.read_text())
            if not record.get('jobId') or scheduler_backend.status(record)['state'] not in ('COMPLETED','FAILED','CANCELLED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED','BOOT_FAIL','DEADLINE'):
                raise ValueError('计算节点任务尚未确认停止，未恢复输出')
        spec=json.loads(row['spec'])
        if not spec.get('pipeline'):raise ValueError('此任务没有可续跑的固定步骤')
        validate_job(spec,config(db))
        if conda_runtime.is_ref(spec['image']) and task_process_alive(directory):raise ValueError('任务进程仍在运行，不能恢复输出')
        if not conda_runtime.is_ref(spec['image']) and json.loads(command(['podman','ps','--filter','name=^biodsh-'+job+'$','--format','json'])):raise ValueError('任务容器仍在运行，不能恢复输出')
        if not (directory/'checkpoint.json').is_file():raise ValueError('尚无已完成步骤的续跑快照，请保留日志并重新准备任务')
        saved=json.loads((directory/'checkpoint.json').read_text())
        if saved.get('spec')!=spec or not re.fullmatch(r'checkpoint-[a-f0-9-]{36}',saved.get('directory','')):raise ValueError('续跑快照与原计划不一致')
        completed=saved.get('completed',[]);steps=[s['step'] for s in spec['pipeline']]
        if not completed or completed!=steps[:len(completed)]:raise ValueError('已完成步骤记录无效')
        snapshot=directory/saved['directory']/'output'
        if snapshot.is_symlink() or snapshot.parent.is_symlink() or tree_manifest(snapshot,directory/'output')!=saved['files']:raise ValueError('续跑快照校验失败，原结果保持不变')
        if sum(f.get('size',0) for f in saved['files'])>disk_available():raise ValueError('空间不足，原结果保持不变')
        staged=directory/('restore-'+str(uuid.uuid4()));shutil.copytree(snapshot,staged,symlinks=True)
        previous=directory/('attempt-'+str(uuid.uuid4()))
        had_output=(directory/'output').exists()
        if had_output:os.rename(directory/'output',previous)
        try:os.rename(staged,directory/'output')
        except BaseException:
            if had_output:os.rename(previous,directory/'output')
            raise
        marker=directory/'resume.tmp';marker.write_text(json.dumps(saved),encoding='utf-8');os.replace(marker,directory/'resume.json')
        if (directory/'cancel').exists():os.rename(directory/'cancel',directory/('cancel-'+str(uuid.uuid4())))
        if scheduler.exists():
            attempt=directory/('scheduler-attempt-'+str(uuid.uuid4()));attempt.mkdir()
            for name in ('scheduler.json','job.sh','scheduler.log'):
                if (directory/name).exists():os.rename(directory/name,attempt/name)
        update(db,job,'queued','从已校验步骤继续；此前输出保留在 attempt 目录')
    launch(job)
    return {'id':job,'state':'queued','completed':completed,'preservedAttempt':previous.name if had_output else None}


def run_job(job):
    import fcntl
    db = database()
    directory = jobdir(job)
    with (directory/'supervisor.lock').open('a') as supervisor:
        try:
            fcntl.flock(supervisor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
        if not row or row['state'] not in ('queued', 'running'):
            return
        if row['state'] == 'running':
            # Do not rerun a task whose SSH acknowledgement or supervisor was lost.
            cancel_container(job)
            update(db, job, 'interrupted', 'Supervisor restarted; outputs retained, explicit new task required')
            return
        spec = json.loads(row['spec'])
        try:
            with (root()/'execution.lock').open('a') as lock:
                while True:
                    if (directory/'cancel').exists():
                        update(db, job, 'cancelled'); return
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        if admission(db, spec):
                            update(db, job, 'running')
                            fcntl.flock(lock, fcntl.LOCK_UN)
                            break
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    except BlockingIOError:
                        pass
                    time.sleep(2)
                protection()
                cfg = config(db)
                validate_job(spec, cfg)
                started=time.time();state='succeeded';detail=''
                pipeline=spec.get('pipeline') or [None]
                resumed=read_resume(directory,spec)
                with (directory/'log.txt').open('ab') as log:
                    for stage in pipeline:
                        if stage and stage['step'] in resumed:continue
                        if (directory/'cancel').exists():state='cancelled';break
                        actual=dict(spec)
                        if stage:
                            actual['image']=stage['image'];actual['argv']=spec['argv']+[stage['step']]
                            log.write(('\n[STEP] '+stage['step']+'\n').encode());log.flush()
                        code,stopping=execute_container(job,actual,cfg,directory,started,log)
                        if (directory/'cancel').exists():state='cancelled';break
                        if code!=0 or stopping:state='failed';detail='exit='+str(code);break
                        if stage:
                            resumed.append(stage['step'])
                            checkpoint(directory,spec,resumed)
                if 'workflow' in spec and state!='cancelled':
                    import workflow_engine
                    state,detail=workflow_engine.finish(types.SimpleNamespace(**globals()),job,state=='succeeded')
                update(db,job,state,detail)
        except Exception as e:
            with contextlib.suppress(Exception):
                cancel_container(job)
            update(db, job, 'failed', str(e)[-2000:])


def launch(job):
    path = Path(__file__).resolve()
    db=database()
    row=db.execute('SELECT * FROM jobs WHERE id=?',(identifier(job),)).fetchone()
    if not row or row['state'] not in ('queued','running'):return
    directory=jobdir(job)
    record_path=directory/'scheduler.json'
    if record_path.exists():
        record=scheduler_backend.recover(directory)
        if not record.get('jobId'):raise ValueError('调度提交状态待核实，未重复启动')
        if (directory/'cancel').exists():scheduler_backend.cancel(record)
        state=scheduler_backend.status(record)
        if state['state'] in ('COMPLETED','FAILED','CANCELLED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','PREEMPTED','BOOT_FAIL','DEADLINE'):
            # The worker commits its result before Slurm reports completion.
            current=db.execute('SELECT state FROM jobs WHERE id=?',(job,)).fetchone()
            if current['state'] in ('queued','running'):
                update(db,job,'cancelled' if state['state']=='CANCELLED' else 'interrupted',
                       '计算节点任务已结束，输出与已完成步骤保留：'+state['state'])
        return
    cluster=scheduler_backend.cluster()
    if cluster:
        spec=json.loads(row['spec'])
        scheduler_backend.submit(directory,[sys.executable,str(path),'--run',job],
            {k:spec[k] for k in ('cpu','memoryMb','timeoutSeconds')},SERVER_KEY,cluster)
        return
    subprocess.Popen([sys.executable, str(path), '--run', identifier(job)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def dispatch(req):
    db = database(); cfg = config(db); action = req['action']; body = req.get('body') or {}
    if action.startswith('server_delete'):
        import server_delete
        return server_delete.dispatch(types.SimpleNamespace(**globals()),action,body,req.get('actor','user'))
    if action in ('initialize','setup_start','setup_status','server_environments'):
        import server_setup
        return server_setup.dispatch(types.SimpleNamespace(**globals()),action,body)
    if action.startswith('workspace_') or action=='server_files':
        import server_workspace
        return server_workspace.dispatch(types.SimpleNamespace(**globals()),action,body)
    if action in ('server_write','server_copy','server_write_requests','server_write_confirm','server_write_reject'):
        import server_file_ops
        return server_file_ops.dispatch(types.SimpleNamespace(**globals()),action,body,req.get('actor','user'))
    if action.startswith('workflow_'):
        import workflow_engine
        return workflow_engine.dispatch(types.SimpleNamespace(**globals()), action, body, req.get('actor','user'))
    if action == 'bootstrap':
        import server_setup
        progress=lambda message:server_setup.progress(types.SimpleNamespace(**globals()),message)
        progress('正在创建 OMaaX 专属目录')
        import server_workspace
        server_workspace.workspace(types.SimpleNamespace(**globals()))
        if os.getuid() == 0:
            raise ValueError('请使用普通 SSH 用户；不以 root 启用分析执行')
        shared = workspace_root()/'files/shared'
        if shared.is_symlink(): raise ValueError('共享目录不能是符号链接')
        shared.mkdir(mode=0o700, parents=True, exist_ok=True)
        sources = cfg.get('sources', {})
        sources.setdefault('shared', str(shared))
        db.execute('INSERT OR REPLACE INTO config VALUES(?,?)', ('sources', json.dumps(sources)))
        audit(db, 'bootstrap.shared_ready', {'path':str(shared),'uid':os.getuid()})
        import shutil
        progress('正在检查并按需安装基础运行组件')
        user_runtime.install()
        checked_protection=protection()
        progress('正在配置普通用户 Conda 分析环境')
        images=cfg.get('images',[]);probe_image=next((i for i in images if conda_runtime.is_ref(i)),None)
        if probe_image:
            try:conda_runtime.resolve(types.SimpleNamespace(**globals()),probe_image)
            except (ValueError,OSError):probe_image=None
        if not probe_image:
            with (root()/'conda-bootstrap.log').open('ab') as log:probe_image=conda_runtime.create(types.SimpleNamespace(**globals()),['python=3.12','pyyaml'],['conda-forge'],log)
        images=list(dict.fromkeys([probe_image,*images]))
        db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',('images',json.dumps(images)))
        server_setup.verify_environment(types.SimpleNamespace(**globals()),probe_image)
        audit(db,'bootstrap.ready',{'uid':os.getuid(),'verifiedImage':probe_image})
        db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',('protectionCache',json.dumps({'at':time.time(),'value':checked_protection})));db.commit()
        return dispatch({'action':'status'})
    if action == 'status':
        cached=cfg.get('protectionCache',{})
        if cached.get('value',{}).get('isolation') is False and 0<=time.time()-cached.get('at',0)<21600 and not body.get('recheck'):
            state=cached['value'];error=''
        elif not cfg.get('images') and not body.get('recheck'):
            state=None;error='基础环境尚未就绪，可在后台按需配置；SSH 连接不受影响'
        else:
            try:state = protection(); error = ''
            except Exception as e:state = None; error = str(e)
        import workflow_engine
        workflow_images=workflow_engine.available_images(types.SimpleNamespace(**globals()))
        workflow_images.update({key:value['image'] for key,value in cfg.get('workflowEnvironments',{}).items() if value.get('image')})
        return {'workflowImages':workflow_images, 'version': VERSION, 'uid': os.getuid(), 'root': str(root()), 'workspace':str(workspace_root()), 'protection': state, 'error': error,
                'sources': cfg.get('sources', {}), 'images': cfg.get('images', []), 'environmentCheckedAt':cached.get('at')}
    if action == 'configure':
        sources = body.get('sources', {}); images = body.get('images', [])
        if not isinstance(sources, dict) or len(sources)>100 or not isinstance(images, list) or len(images)>100:
            raise ValueError('Invalid configuration')
        sources = {identifier(k): str(source_path(v, imported=k.startswith('import_') and cfg.get('sources', {}).get(k)==v)) for k,v in sources.items()}
        if any(not immutable_image(i) for i in images):
            raise ValueError('Approve images by immutable sha256 digest')
        for key, value in [('sources', sources), ('images', images)]:
            db.execute('INSERT OR REPLACE INTO config VALUES(?,?)', (key, json.dumps(value)))
        audit(db, 'configure', {'sources': sources, 'images': images})
        return {'saved': True}
    if action == 'submit':
        protection(); spec = validate_job(body, cfg); job = spec['id']
        existing = db.execute('SELECT spec FROM jobs WHERE id=?', (job,)).fetchone()
        if existing:
            if json.loads(existing['spec']) != spec:
                raise ValueError('Task ID already used by another request')
            launch(job); return {'id':job, 'reused':True}
        # Never pull an image during a task; the user provisions approved images explicitly.
        if conda_runtime.is_ref(spec['image']):conda_runtime.resolve(types.SimpleNamespace(**globals()),spec['image'])
        else:command(['podman', 'image', 'inspect', spec['image']])
        directory = jobdir(job,spec); directory.mkdir(parents=True, mode=0o700)
        (directory/'output').mkdir(mode=0o700)
        now = time.time()
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', (job, json.dumps(spec), 'queued', now, now, ''))
        db.execute('INSERT INTO job_locations VALUES(?,?)',(job,directory.relative_to(workspace_root()).as_posix()))
        (directory/'task.json').write_text(json.dumps({'id':job,'context':spec.get('context'),'title':spec.get('title','分析任务'),'resources':{key:spec[key] for key in ('cpu','memoryMb','timeoutSeconds')}}),encoding='utf-8')
        audit(db, 'job.queued', {'id': job, 'uid': os.getuid(), 'protection':'conda-preserve-inputs'})
        launch(job); return {'id': job}
    if action == 'jobs':
        rows = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 200')]
        for r in rows:
            r['spec'] = json.loads(r['spec'])
            if r['state'] in ('queued', 'running'):
                launch(r['id'])
                current=db.execute('SELECT state,detail,updated FROM jobs WHERE id=?',(r['id'],)).fetchone()
                r.update(dict(current))
        return rows
    if action == 'cancel':
        job = identifier(body['id'])
        row = db.execute('SELECT state FROM jobs WHERE id=?', (job,)).fetchone()
        if not row: raise ValueError('Unknown task')
        if row['state'] in ('queued', 'running'):
            (jobdir(job)/'cancel').touch(exist_ok=True)
            audit(db, 'job.cancel_requested', {'id':job}); launch(job)
        return {'id':job}
    if action == 'logs':
        directory=jobdir(body['id'])
        name=body.get('segment','log.txt')
        if not isinstance(name,str) or not re.fullmatch(r'log(?:-[0-9]+-[a-f0-9]{8})?\.txt',name):raise ValueError('日志分段无效')
        path=contained(directory,name) if (directory/name).exists() else directory/name
        segments=[p.name for p in sorted(directory.glob('log-*.txt')) if not p.is_symlink()]
        if not path.exists(): return {'text':'','segments':segments}
        offset=body.get('offset',max(0,path.stat().st_size-65536))
        if type(offset) is not int or offset<0:raise ValueError('日志位置无效')
        with path.open('rb') as f:
            f.seek(offset);text=f.read(65536).decode('utf8','replace')
            return {'text':text,'segments':segments+['log.txt'],'segment':name,'nextOffset':f.tell(),'size':path.stat().st_size}
    if action == 'audit':
        return [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 300')]
    if action == 'export':
        if body.get('job'):
            row = db.execute('SELECT state FROM jobs WHERE id=?', (identifier(body['job']),)).fetchone()
            if not row or row['state'] in ('running','queued'): raise ValueError('Task output is not settled')
            base = (jobdir(body['job'])/'output').resolve(strict=True)
        else:
            name = identifier(body['source'])
            base = source_path(cfg.get('sources', {})[name], imported=name.startswith('import_'))
        paths=body.get('paths')
        if paths is not None and (not isinstance(paths,list) or not 1<=len(paths)<=500 or not body.get('job')):
            raise ValueError('Select 1–500 settled task result files')
        path = contained(base, body.get('path', '.'))
        files = []
        items = [contained(base,name) for name in paths] if paths is not None else (path.rglob('*') if path.is_dir() else [path])
        for item in items:
            if item.is_symlink(): raise ValueError('Symlinks are not transferable')
            if item.is_dir(): continue
            if not item.is_file(): raise ValueError('Special files are not transferable')
            name = str(item.relative_to(base)) if paths is not None else (str(item.relative_to(path)) if path.is_dir() else item.name)
            info=item.stat();files.append({'path':str(item), 'name':name, 'size':info.st_size, 'mtime':int(info.st_mtime)})
            if len(files)>500: raise ValueError('Select a smaller directory (maximum 500 files per transfer)')
        audit(db, 'data.export', {'source':str(path), 'files':len(files)})
        return files
    if action == 'import_prepare':
        transfer = identifier(body['id'])
        existing=db.execute('SELECT path FROM transfer_imports WHERE id=?',(transfer,)).fetchone()
        if existing:
            directory=Path(existing['path'])
            if not directory.is_dir() or any(p.is_symlink() for p in [directory,*directory.parents]):raise ValueError('接收目录发生变化，未继续传输')
            return {'path':str(directory)}
        size=body.get('bytes',0)
        if type(size) is not int or size<0 or size>disk_available():
            raise ValueError('目标可用空间不足（保留至少 1 GiB / 1%），未开始传输')
        import workspace_context
        directory = workspace_context.base(types.SimpleNamespace(**globals()),body.get('context'))/'files/imports'/transfer
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        db.execute('INSERT INTO transfer_imports VALUES(?,?)',(transfer,str(directory)));db.commit()
        audit(db, 'data.import_pending', {'id':transfer})
        return {'path':str(directory)}
    if action == 'import_finish':
        transfer = identifier(body['id']);saved=db.execute('SELECT path FROM transfer_imports WHERE id=?',(transfer,)).fetchone()
        directory = Path(saved['path']) if saved else root()/'imports'/transfer
        directory=contained(directory,'.')
        files = body['files']
        for entry in files:
            p = contained(directory, entry['name'])
            digest = hashlib.sha256()
            with p.open('rb') as f:
                while block := f.read(1024*1024): digest.update(block)
            if p.stat().st_size != entry['size'] or digest.hexdigest() != entry['sha256']:
                raise ValueError('Transfer checksum mismatch; incomplete files retained, not registered')
        # Imported roots are generated here, not arbitrary paths supplied by the agent.
        db.execute('BEGIN IMMEDIATE')
        sources = config(db).get('sources', {}); sources['import_'+transfer] = str(directory)
        db.execute('INSERT OR REPLACE INTO config VALUES(?,?)', ('sources', json.dumps(sources)))
        audit(db, 'data.import_complete', {'id':transfer, 'files':len(files)})
        return {'source':'import_'+transfer, 'path':str(directory)}
    raise ValueError('Unsupported action')


if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1] == '--conda-exec':
        import resource
        payload=json.loads(sys.argv[2]);limit=payload.pop('memoryMb')*1048576;cpu=payload.pop('cpu')
        resource.setrlimit(resource.RLIMIT_AS,(limit,limit));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        if hasattr(os,'sched_getaffinity'):os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:cpu])
        sys.exit(conda_runtime.network_run(Path(payload.pop('opt')),log=sys.stdout,writable=False,**payload))
    elif len(sys.argv)>1 and sys.argv[1] == '--setup':
        import server_setup
        server_setup.run(types.SimpleNamespace(**globals()),sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1] == '--prepare':
        import workflow_environments
        workflow_environments.run(types.SimpleNamespace(**globals()),sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1] == '--workflow-preflight':
        import workflow_preflight
        workflow_preflight.run(types.SimpleNamespace(**globals()),sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1] == '--run':
        run_job(sys.argv[2])
    else:
        try:
            print(json.dumps({'result':dispatch(json.loads(sys.stdin.read()))}))
        except Exception as e:
            print(json.dumps({'error':str(e)}))
