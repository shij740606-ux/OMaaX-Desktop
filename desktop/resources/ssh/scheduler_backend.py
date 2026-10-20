"""Slurm submission and reconciliation. Never execute a rejected batch job locally."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or '调度请求失败')[-2000:])
    return result.stdout.strip()


def cluster():
    if not shutil.which('sbatch'):
        return None
    # A client binary alone is not evidence of an accessible cluster.
    config = command(['scontrol', 'show', 'config'])
    match = re.search(r'(?m)^\s*ClusterName\s*=\s*(\S+)', config)
    if not match:
        raise ValueError('检测到调度客户端，但无法核实集群；未在连接节点运行分析')
    return match.group(1)


def allocation():
    value = os.environ.get('SLURM_JOB_ID', '')
    if not re.fullmatch(r'[0-9]+', value):
        return None
    record = command(['scontrol', 'show', 'job', value, '-o'])
    if not re.search(r'\bUserId=\S+\(' + str(os.getuid()) + r'\)', record):
        raise ValueError('当前调度分配不属于此账号')
    if not re.search(r'\bJobState=(RUNNING|COMPLETING)\b', record):
        raise ValueError('当前调度分配未运行')
    nodes = re.search(r'\bNodeList=(\S+)', record)
    if not nodes:
        raise ValueError('无法核实计算节点')
    allocated = command(['scontrol', 'show', 'hostnames', nodes.group(1)]).splitlines()
    import socket
    if socket.gethostname().split('.')[0] not in [n.split('.')[0] for n in allocated]:
        raise ValueError('当前连接节点不在调度分配内')
    return value


def atomic(path, value):
    temp = path.with_name(path.name + '.tmp-' + str(os.getpid()))
    with temp.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+', value):
        raise ValueError('调度作业编号无效')
    return value


def status(record):
    identity = job_id(record['jobId'])
    try:
        live = command(['squeue', '--noheader', '--jobs', identity, '--format', '%i|%T|%R'])
    except ValueError as error:
        if 'Invalid job id' not in str(error):raise
        live=''
    for line in live.splitlines():
        fields = line.split('|', 2)
        if len(fields) == 3 and fields[0] == identity:
            return {'state': fields[1], 'reason': fields[2], 'jobId': identity}
    historical = command(['sacct', '-nP', '-j', identity, '--format=JobIDRaw,State,ExitCode'])
    for line in historical.splitlines():
        fields = line.split('|')
        if len(fields) >= 3 and fields[0] == identity:
            return {'state': fields[1].split()[0].rstrip('+'), 'exitCode': fields[2], 'jobId': identity}
    return {'state': 'UNKNOWN', 'reason': '等待调度记账，未重新提交', 'jobId': identity}

def recover(directory):
    """Reconcile a lost acknowledgement by the unique persisted job name."""
    from datetime import datetime
    import getpass
    path=Path(directory)/'scheduler.json'
    record=json.loads(path.read_text())
    if record.get('jobId'):return record
    name='omaax-'+record['digest'][:16]
    candidates=set()
    live=command(['squeue','--noheader','--user',str(os.getuid()),'--name',name,'--format','%i|%j'])
    for line in live.splitlines():
        fields=line.split('|')
        if len(fields)==2 and fields[1]==name and re.fullmatch(r'[0-9]+',fields[0]):candidates.add(fields[0])
    since=datetime.fromtimestamp(record['created']-60).strftime('%Y-%m-%dT%H:%M:%S')
    history=command(['sacct','-nP','--user',getpass.getuser(),'--name',name,'--starttime',since,'--format=JobIDRaw,JobName%64'])
    for line in history.splitlines():
        fields=line.split('|')
        if len(fields)>=2 and fields[1]==name and re.fullmatch(r'[0-9]+',fields[0]):candidates.add(fields[0])
    if len(candidates)>1:raise ValueError('发现多个匹配的调度任务，保留记录并暂停恢复')
    if candidates:
        record.update(jobId=candidates.pop(),state='SUBMITTED')
        atomic(path,record)
    return record


def submit(directory, argv, resources, server_key, cluster_name):
    import fcntl
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(p.is_symlink() for p in [directory, *directory.parents]):
        raise ValueError('调度记录目录不能是链接')
    if not re.fullmatch(r'[a-f0-9]{16}', server_key):
        raise ValueError('服务器标识无效')
    for field, low, high in [('cpu', 1, 4096), ('memoryMb', 128, 1048576), ('timeoutSeconds', 10, 604800)]:
        if type(resources.get(field)) is not int or not low <= resources[field] <= high:
            raise ValueError('调度资源参数无效')
    spec = {'argv': argv, 'resources': resources, 'serverKey': server_key, 'cluster': cluster_name}
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    record_path = directory / 'scheduler.json'
    with (directory / 'submit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if record_path.exists():
            record = json.loads(record_path.read_text(encoding='utf-8'))
            if record['digest'] != digest:
                raise ValueError('此调度提交已有不同内容，需创建新的执行尝试')
            if record.get('jobId'):
                return record
            # A response may have been lost after Slurm accepted the job. Do not
            # resubmit an uncertain launch, even when accounting is delayed.
            record=recover(directory)
            if record.get('jobId'):return record
            raise ValueError('调度提交状态待核实，已保留提交标识，未重复启动')
        record = {'digest': digest, 'cluster': cluster_name, 'state': 'SUBMITTING', 'created': time.time()}
        atomic(record_path, record)
        script = directory / 'job.sh'
        source = '#!/bin/sh\nset -eu\nexport OMAAX_SERVER_KEY=' + shlex.quote(server_key)
        source += '\nexec ' + ' '.join(shlex.quote(str(v)) for v in argv) + '\n'
        with script.open('x', encoding='utf-8') as handle:
            handle.write(source)
        minutes = max(1, (resources['timeoutSeconds'] + 59) // 60)
        args = ['sbatch', '--parsable', '--job-name=omaax-' + digest[:16], '--nodes=1', '--ntasks=1',
                '--cpus-per-task=' + str(resources['cpu']), '--mem=' + str(resources['memoryMb']) + 'M',
                '--time=' + str(minutes), '--output=' + str(directory / 'scheduler.log'),
                '--chdir=' + str(directory), str(script)]
        try:
            output = command(args)
            identity = job_id(output.split(';')[0])
            record.update(jobId=identity, state='SUBMITTED')
            atomic(record_path, record)
            return record
        except Exception:
            record['state'] = 'SUBMISSION_UNKNOWN'
            atomic(record_path, record)
            raise


def cancel(record):
    identity = job_id(record['jobId'])
    command(['scancel', identity])
    return {'jobId': identity, 'state': 'CANCELLING'}
