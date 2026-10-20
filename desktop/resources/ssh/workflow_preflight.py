"""Durable compute-node preparation; a desktop disconnect never loses submission."""
import json
import os
from pathlib import Path
import socket
import sys
import time
import uuid
import scheduler_backend as scheduler


def directory(r, identity):
    identity = str(uuid.UUID(identity))
    path = r.root() / 'workflow-preflight' / identity
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('流程准备目录不能是链接')
    return path


def start(r, request):
    import workflow_engine as engine
    if set(request) - {'body', 'intent', 'actor'}:
        raise ValueError('流程准备请求无效')
    body = request['body']
    engine.no_extra(body, engine.PARAMETERS)
    flow = engine.flow_for(body.get('workflow'))
    engine.select_steps(flow, body.get('steps'))
    intent = request.get('intent')
    if intent is not None and (not isinstance(intent, dict) or intent.get('source') != 'user' or intent.get('executionRequested') is not True):
        raise ValueError('缺少真实用户任务指令')
    cluster = scheduler.cluster()
    if not cluster:
        result = engine.dispatch(r, 'workflow_plan', body, request.get('actor', 'user'))
        return complete(r, result, intent)
    # A tool retry for the same real user message must not start a second run.
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, engine.digest(
        {'request': request, 'engine': engine.ENGINE_HASH, 'server': r.SERVER_KEY}
    ))) if intent is not None else str(uuid.uuid4())
    path = directory(r, identity)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    import fcntl
    with (path / 'initialize.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (path / 'request.json').exists():
            scheduler.atomic(path / 'request.json', request)
            scheduler.atomic(path / 'state.json', {'state': 'preparing', 'created': time.time()})
        elif (path / 'result.json').exists():
            return json.loads((path / 'result.json').read_text())
        elif json.loads((path / 'state.json').read_text())['state'] == 'preparation_failed':
            raise ValueError(json.loads((path / 'state.json').read_text()).get('detail', '输入准备失败，请查看流程记录'))
    # This worker remains alive independently of the app and submits the analysis
    # only after input validation and the immutable plan have been written.
    record = scheduler.submit(path, [sys.executable, r.__file__, '--workflow-preflight', identity],
                              {'cpu': 1, 'memoryMb': 2048, 'timeoutSeconds': 7200}, r.SERVER_KEY, cluster)
    return {'operationId': identity, 'state': 'preparing', 'schedulerJobId': record['jobId'],
            'confirmed': False, 'detail': '正在计算节点准备输入；完成后自动继续', 'context': body.get('context')}


def complete(r, result, intent):
    if intent is not None:
        task = r.dispatch({'action': 'workflow_confirm', 'actor': 'user',
                           'body': {'id': result['plan']['id'], 'digest': result['digest'], 'intent': intent}})
        return {**result, 'confirmed': True, 'state': 'queued', 'task': task}
    return result


def resume(r, body, cluster):
    import workflow_engine as engine
    engine.no_extra(body, {'id', 'digest'})
    job = r.identifier(body.get('id'))
    row = r.database().execute('SELECT state FROM jobs WHERE id=?', (job,)).fetchone()
    if not row or row['state'] not in ('failed', 'cancelled', 'interrupted', 'invalid_result'):
        raise ValueError('只有已停止的任务可以续跑')
    previous = r.jobdir(job) / 'scheduler.json'
    record = json.loads(previous.read_text()) if previous.exists() else {}
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, engine.digest(
        {'resume': body, 'attempt': record, 'engine': engine.ENGINE_HASH})))
    path = directory(r, identity)
    base_identity = identity
    for attempt in range(1, 33):
        state_path = path / 'state.json'
        if not state_path.exists() or json.loads(state_path.read_text())['state'] != 'preparation_failed':
            break
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, base_identity + ':' + str(attempt)))
        path = directory(r, identity)
    else:
        raise ValueError('连续恢复失败次数过多，请先检查日志和输入')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    import fcntl
    with (path / 'initialize.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (path / 'request.json').exists():
            scheduler.atomic(path / 'request.json', {'resume': body})
            scheduler.atomic(path / 'state.json', {'state': 'preparing', 'created': time.time()})
    task = scheduler.submit(path, [sys.executable, r.__file__, '--workflow-preflight', identity],
                            {'cpu': 1, 'memoryMb': 2048, 'timeoutSeconds': 7200}, r.SERVER_KEY, cluster)
    return {'id': job, 'operationId': identity, 'state': 'preparing', 'schedulerJobId': task['jobId']}


def run(r, identity):
    import fcntl
    path = directory(r, identity)
    with (path / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads((path / 'state.json').read_text())
        if state['state'] == 'complete':
            return
        try:
            allocation = scheduler.allocation()
            if not allocation:
                raise ValueError('流程预处理必须在计算节点运行')
            request = json.loads((path / 'request.json').read_text())
            scheduler.atomic(path / 'placement.json', {'hostname': socket.gethostname(),
                             'jobId': allocation, 'started': time.time(), 'phase': 'input-validation-and-plan'})
            if 'resume' in request:
                result = r.dispatch({'action': 'workflow_resume', 'actor': 'user', 'body': request['resume']})
                scheduler.atomic(path / 'result.json', result)
                scheduler.atomic(path / 'state.json', {'state': 'complete', 'finished': time.time()})
                return
            # A persisted plan allows a restarted worker to reconcile submission
            # without making another analysis task.
            result_path = path / 'plan.json'
            if result_path.exists():
                result = json.loads(result_path.read_text())
            else:
                result = r.dispatch({'action': 'workflow_plan', 'body': request['body'],
                                     'actor': request.get('actor', 'user')})
                scheduler.atomic(result_path, result)
            result = complete(r, result, request.get('intent'))
            scheduler.atomic(path / 'result.json', result)
            scheduler.atomic(path / 'state.json', {'state': 'complete', 'finished': time.time()})
        except Exception as error:
            scheduler.atomic(path / 'state.json', {'state': 'preparation_failed',
                             'detail': str(error)[-2000:], 'finished': time.time()})
            raise


def pending(r):
    import workflow_engine as engine
    parent = r.root() / 'workflow-preflight'
    if not parent.exists():
        return []
    rows = []
    for path in sorted(parent.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        directory(r, path.name)
        state = json.loads((path / 'state.json').read_text())
        if state['state'] == 'complete':
            continue
        if state['state'] == 'preparing' and (path / 'scheduler.json').exists():
            record = scheduler.recover(path)
            if record.get('jobId'):
                status = scheduler.status(record)['state']
                if status in ('FAILED', 'CANCELLED', 'TIMEOUT', 'NODE_FAIL', 'OUT_OF_MEMORY', 'PREEMPTED', 'BOOT_FAIL', 'COMPLETED'):
                    # Re-read: the worker may have committed while Slurm was queried.
                    state = json.loads((path / 'state.json').read_text())
                    if state['state'] == 'complete':
                        continue
                    state = {'state': 'preparation_failed', 'detail': '输入准备已停止，日志与原文件保留：' + status}
        request = json.loads((path / 'request.json').read_text())
        if 'resume' in request:
            row = r.database().execute('SELECT plan FROM workflow_plans WHERE id=?', (request['resume']['id'],)).fetchone()
            body = json.loads(row['plan'])
        else:
            body = request['body']
        flow = engine.flow_for(body['workflow'])
        rows.append({'plan': {**body, 'id': path.name, 'name': flow['name'], 'prefix': body.get('prefix', 'omaax'),
                             'steps': body.get('steps', flow['defaultSteps'])},
                     'digest': '', 'confirmed': False, **state})
    return rows
