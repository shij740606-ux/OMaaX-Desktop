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

_machine = Path('/etc/machine-id')
_identity = (_machine.read_text().strip() if _machine.exists() else '') + ':' + platform.node()
# Shared NFS home directories must not merge two servers' queues and execution locks.
ROOT = Path.home() / '.local/share/biodsh-personal' / hashlib.sha256(_identity.encode()).hexdigest()[:16]
VERSION = 1


def immutable_image(value):
    return isinstance(value, str) and re.fullmatch(r'(?:[^\s]+@)?sha256:[a-f0-9]{64}', value)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
        raise ValueError('Invalid identifier')
    return value


def root():
    # A remote account can change its own files; never trust a redirected control directory.
    for part in [ROOT, *ROOT.parents]:
        if part.is_symlink():
            raise ValueError('Control directory must not contain symlinks')
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ROOT.stat().st_uid != os.getuid():
        raise ValueError('Control directory owner mismatch')
    os.chmod(ROOT, 0o700)
    return ROOT


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
    if os.getuid() == 0:
        raise ValueError('Use a non-root SSH account for protected execution')
    info = json.loads(command(['podman', 'info', '--format', 'json']))
    host = info.get('host', {})
    if not host.get('security', {}).get('rootless') or host.get('cgroupVersion') != 'v2':
        raise ValueError('Rootless Podman and cgroup v2 are required; no unsafe fallback')
    controllers = host.get('cgroupControllers', [])
    if not {'cpu', 'memory'}.issubset(controllers):
        raise ValueError('CPU/memory cgroup controllers unavailable to this SSH user')
    return {'rootless': True, 'cgroup': 'v2', 'uid': os.getuid()}


def source_path(value, imported=False):
    p = Path(value).expanduser()
    if not p.is_absolute() or any(x.is_symlink() for x in [p, *p.parents]):
        raise ValueError('Select an absolute, non-symlink data directory')
    p = p.resolve(strict=True)
    managed_import = (imported and p.parent == ROOT/'imports' and re.fullmatch(r'[a-f0-9-]{36}', p.name)) or p == ROOT/'shared'
    managed_workspace = p == ROOT/'workspace' or ROOT/'workspace' in p.parents
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
        if row:path=server_workspace.managed(types.SimpleNamespace(**globals()),row['path'],existing=False)
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
    args = ['podman', 'run', '--name', 'biodsh-'+spec['id'], '--label', 'biodsh.personal='+spec['id'],
            '--pull=never', '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user=0:0', '--pids-limit='+str(max(512,min(4096,256+spec['cpu']*32))), '--cpus='+str(spec['cpu']), '--memory='+str(spec['memoryMb'])+'m',
            '--memory-swap='+str(spec['memoryMb'])+'m', '--tmpfs=/tmp:rw,nosuid,nodev,size=256m',
            '--volume', str(out)+':/output:rw', '--workdir=/output']
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


def cancel_container(job):
    name = 'biodsh-'+identifier(job)
    try:
        label = command(['podman', 'inspect', '--format', '{{ index .Config.Labels "biodsh.personal" }}', name]).strip()
    except (ValueError, FileNotFoundError):
        return False
    if label != job:
        raise ValueError('Container ownership label mismatch')
    command(['podman', 'stop', '--time', '5', name])
    return True


def execute_container(job,spec,cfg,directory,started,log):
    args=container_args(spec,cfg)
    if 'workflow' in spec:args.insert(2,'--rm')
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    poller=selectors.DefaultSelector();poller.register(process.stdout,selectors.EVENT_READ)
    written=log.tell();stopping=False
    while process.poll() is None:
        if not stopping and ((directory/'cancel').exists() or time.time()-started>spec['timeoutSeconds'] or disk_available()==0):
            stopping=cancel_container(job)
        for key,_ in poller.select(0.5):
            chunk=os.read(key.fileobj.fileno(),65536)
            if not chunk:poller.unregister(key.fileobj)
            if written<10*1024*1024:
                log.write(chunk[:10*1024*1024-written]);log.flush();written+=len(chunk)
    while chunk:=process.stdout.read(65536):
        if written<10*1024*1024:log.write(chunk[:10*1024*1024-written]);written+=len(chunk)
    process.stdout.close();poller.close()
    return process.returncode,stopping


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
                    if time.time()-row['created'] > spec['timeoutSeconds']:
                        update(db, job, 'failed', 'Queue timeout'); return
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        if memory() >= spec['memoryMb']*1048576 and os.getloadavg()[0]+spec['cpu'] <= (os.cpu_count() or 1)+0.5:
                            break
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    except BlockingIOError:
                        pass
                    time.sleep(2)
                protection()
                cfg = config(db)
                validate_job(spec, cfg)
                update(db, job, 'running')
                started=time.time();state='succeeded';detail=''
                pipeline=spec.get('pipeline') or [None]
                with (directory/'log.txt').open('ab') as log:
                    for stage in pipeline:
                        if (directory/'cancel').exists():state='cancelled';break
                        actual=dict(spec)
                        if stage:
                            actual['image']=stage['image'];actual['argv']=spec['argv']+[stage['step']]
                            log.write(('\n[STEP] '+stage['step']+'\n').encode());log.flush()
                        code,stopping=execute_container(job,actual,cfg,directory,started,log)
                        if (directory/'cancel').exists():state='cancelled';break
                        if code!=0 or stopping:state='failed';detail='exit='+str(code);break
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
    subprocess.Popen([sys.executable, str(path), '--run', identifier(job)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def dispatch(req):
    db = database(); cfg = config(db); action = req['action']; body = req.get('body') or {}
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
        if os.getuid() == 0:
            raise ValueError('请使用普通 SSH 用户；不以 root 启用分析执行')
        shared = root()/'shared'
        if shared.is_symlink(): raise ValueError('共享目录不能是符号链接')
        shared.mkdir(mode=0o700, exist_ok=True)
        sources = cfg.get('sources', {})
        sources.setdefault('shared', str(shared))
        db.execute('INSERT OR REPLACE INTO config VALUES(?,?)', ('sources', json.dumps(sources)))
        audit(db, 'bootstrap.shared_ready', {'path':str(shared),'uid':os.getuid()})
        import shutil
        if not shutil.which('podman'):
            if shutil.which('apt-get') and shutil.which('sudo') and subprocess.run(['sudo','-n','true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                audit(db, 'bootstrap.install_packages', {'packages':['podman','uidmap','slirp4netns','fuse-overlayfs']})
                command(['sudo','-n','apt-get','update'], timeout=120)
                command(['sudo','-n','apt-get','install','-y','podman','uidmap','slirp4netns','fuse-overlayfs'], timeout=240)
            else:
                raise ValueError('专用共享目录已准备；自动安装 Podman 需要系统安装权限，请管理员安装 podman、uidmap、slirp4netns、fuse-overlayfs 后重新检查')
        protection()
        images = cfg.get('images', [])
        probe_image = images[0] if images else None
        if not images or body.get('image'):
            image = body.get('image') or 'docker.io/library/python:3.12-slim'
            if not isinstance(image,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,400}',image):
                raise ValueError('镜像地址无效')
            try:
                command(['podman','pull',image],timeout=240)
            except subprocess.TimeoutExpired:
                raise ValueError('默认分析镜像下载超时，自动配置未完成。请检查服务器外网连接，或在数据保护页面指定可访问的 Python 镜像；不会通过本机 VPN 下载')
            details = json.loads(command(['podman','image','inspect',image]))
            pinned = details[0].get('RepoDigests', [])[:1]
            if not pinned: raise ValueError('镜像没有不可变摘要，不能自动批准')
            images = list(dict.fromkeys(images+pinned))
            probe_image = pinned[0]
            db.execute('INSERT OR REPLACE INTO config VALUES(?,?)', ('images',json.dumps(images)))
            audit(db,'bootstrap.image_ready',{'images':images})
        # A real sandbox smoke test catches missing delegated cgroups and rootless prerequisites.
        command(['podman','run','--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL',
                 '--security-opt=no-new-privileges','--userns=keep-id','--cpus=1','--memory=128m','--memory-swap=128m',
                 probe_image,'python','-c','print("biodsh-isolation-ready")'],timeout=60)
        audit(db,'bootstrap.ready',{'uid':os.getuid()})
        return dispatch({'action':'status'})
    if action == 'status':
        try:
            state = protection(); error = ''
        except Exception as e:
            state = None; error = str(e)
        import workflow_engine
        workflow_images=workflow_engine.available_images(types.SimpleNamespace(**globals()))
        workflow_images.update({key:value['image'] for key,value in cfg.get('workflowEnvironments',{}).items() if value.get('image')})
        return {'workflowImages':workflow_images, 'version': VERSION, 'uid': os.getuid(), 'root': str(root()), 'protection': state, 'error': error,
                'sources': cfg.get('sources', {}), 'images': cfg.get('images', [])}
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
        command(['podman', 'image', 'inspect', spec['image']])
        directory = jobdir(job,spec); directory.mkdir(parents=True, mode=0o700)
        (directory/'output').mkdir(mode=0o700)
        now = time.time()
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', (job, json.dumps(spec), 'queued', now, now, ''))
        db.execute('INSERT INTO job_locations VALUES(?,?)',(job,directory.relative_to(root()/'workspace').as_posix()))
        (directory/'task.json').write_text(json.dumps({'id':job,'context':spec.get('context'),'title':spec.get('title','分析任务'),'resources':{key:spec[key] for key in ('cpu','memoryMb','timeoutSeconds')}}),encoding='utf-8')
        audit(db, 'job.queued', {'id': job, 'uid': os.getuid(), 'protection':'rootless-readonly'})
        launch(job); return {'id': job}
    if action == 'jobs':
        rows = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 200')]
        for r in rows:
            r['spec'] = json.loads(r['spec'])
            if r['state'] in ('queued', 'running'):
                launch(r['id'])
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
        path = jobdir(body['id'])/'log.txt'
        if not path.exists(): return {'text':''}
        with path.open('rb') as f:
            f.seek(max(0, path.stat().st_size-65536))
            return {'text':f.read(65536).decode('utf8', 'replace')}
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
            files.append({'path':str(item), 'name':name, 'size':item.stat().st_size})
            if len(files)>500: raise ValueError('Select a smaller directory (maximum 500 files per transfer)')
        audit(db, 'data.export', {'source':str(path), 'files':len(files)})
        return files
    if action == 'import_prepare':
        transfer = identifier(body['id'])
        size=body.get('bytes',0)
        if type(size) is not int or size<0 or size>disk_available():
            raise ValueError('目标可用空间不足（保留至少 1 GiB / 1%），未开始传输')
        directory = root()/'imports'/transfer
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        audit(db, 'data.import_pending', {'id':transfer})
        return {'path':str(directory)}
    if action == 'import_finish':
        transfer = identifier(body['id']); directory = (root()/'imports'/transfer).resolve(strict=True)
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
    if len(sys.argv)>1 and sys.argv[1] == '--prepare':
        import workflow_environments
        workflow_environments.run(types.SimpleNamespace(**globals()),sys.argv[2])
    elif len(sys.argv)>1 and sys.argv[1] == '--run':
        run_job(sys.argv[2])
    else:
        try:
            print(json.dumps({'result':dispatch(json.loads(sys.stdin.read()))}))
        except Exception as e:
            print(json.dumps({'error':str(e)}))
