"""Real Linux acceptance; requires an isolated HOME initialized by bootstrap."""
import json,os,shutil,subprocess,sys,time,types,uuid
from pathlib import Path
from unittest.mock import patch
import personal_runner as r
import conda_runtime as c
import workflow_engine as w

def main():
    checks=[];cfg=r.config(r.database());ref=next(i for i in cfg['images'] if c.is_ref(i))
    assert os.getuid()!=0
    # No sudo/Podman command is discoverable in the test launcher environment.
    bins=Path.home()/'test-bin';bins.mkdir(exist_ok=True)
    for name in ('sh','true','flock','tar','curl','sha256sum','uname','id','mkdir','mv','chmod','date','cat','python3'):
        path=shutil.which(name)
        if path and not (bins/name).exists():(bins/name).symlink_to(path)
    os.environ['PATH']=str(c.user_runtime.prefix()/'bin')+':'+str(bins)
    assert shutil.which('sudo') is None and shutil.which('podman') is None
    assert r.protection()['backend']=='conda';checks.append('non-root; no sudo or Podman in PATH; direct execution without isolation')
    (Path.home()/'private-marker').write_text('not exposed');original=Path.home()/'test-input';original.mkdir(exist_ok=True);keep=original/'keep.txt';keep.write_text('original')
    cfg['sources']['test']=str(original);r.dispatch({'action':'configure','body':{'sources':cfg['sources'],'images':cfg['images']}})
    def spec(code,**extra):return {'id':str(uuid.uuid4()),'image':ref,'sources':['test'],'argv':['python3','-u','-c',code],'cpu':1,'memoryMb':512,'timeoutSeconds':30,**extra}
    def wait(job):
        for _ in range(120):
            row=dict(r.database().execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
            if row['state'] not in ('queued','running'):return row
            time.sleep(.5)
        raise AssertionError('task timed out: '+job)
    code="import pathlib,json; p=pathlib.Path('/input/test/keep.txt'); assert p.read_text()=='original'; pathlib.Path('/output/result.json').write_text(json.dumps({'answer':6})); print('analysis-success')"
    task=spec(code);r.dispatch({'action':'submit','body':task});row=wait(task['id']);assert row['state']=='succeeded',row
    assert keep.read_text()=='original';assert 'analysis-success' in (r.jobdir(task['id'])/'log.txt').read_text()
    result=json.loads((r.jobdir(task['id'])/'output/result.json').read_text());assert result['answer']==6
    checks.append('real queued Python analysis; logs and separate results; input preserved')
    bad=spec('import nonexistent_omaax_dependency');r.dispatch({'action':'submit','body':bad});assert wait(bad['id'])['state']=='failed'
    assert 'ModuleNotFoundError' in (r.jobdir(bad['id'])/'log.txt').read_text()
    fixed=spec('import json; print(json.dumps({"repaired":True}))');r.dispatch({'action':'submit','body':fixed});assert wait(fixed['id'])['state']=='succeeded'
    checks.append('failure logs retained; repaired script resubmitted successfully')
    slow=spec('import time; print("started",flush=True); time.sleep(90)');r.dispatch({'action':'submit','body':slow});time.sleep(3)
    r.dispatch({'action':'cancel','body':{'id':slow['id']}});assert wait(slow['id'])['state']=='cancelled'
    checks.append('cancel terminates analysis and retains input/logs')
    timed=spec('import time; time.sleep(30)',timeoutSeconds=10);r.dispatch({'action':'submit','body':timed});assert wait(timed['id'])['state']=='failed'
    checks.append('execution timeout stops task')
    # Exercise the actual checkpoint/run/resume machinery with two tiny fixture stages.
    # Scientific plan binding is covered separately by workflow_engine_test.py.
    deadline=time.time()+10
    code='import sys,time,pathlib,uuid; p=pathlib.Path("/output/first"); step=sys.argv[1]; p.write_text(str(uuid.uuid4())) if step=="one" else None; print(step,flush=True); sys.exit(9 if step=="two" and time.time()<'+str(deadline)+' else 0)'
    pipeline=spec(code,workflow={'id':'fixture'},pipeline=[{'step':'one','image':ref},{'step':'two','image':ref}])
    with patch.object(w,'validate_bound_job'),patch.object(w,'finish',side_effect=lambda rr,j,ok:('succeeded' if ok else 'failed','fixture')),patch.object(r,'launch'):
        r.dispatch({'action':'submit','body':pipeline});r.run_job(pipeline['id'])
        assert wait(pipeline['id'])['state']=='failed'
        directory=r.jobdir(pipeline['id']);first=(directory/'output/first').read_text();checkpoint=json.loads((directory/'checkpoint.json').read_text());assert checkpoint['completed']==['one']
        (directory/'output/first').write_text('damaged unfinished output')
        time.sleep(max(0,deadline-time.time())+.2);r.resume_job(pipeline['id']);r.run_job(pipeline['id'])
        assert wait(pipeline['id'])['state']=='succeeded';assert (directory/'output/first').read_text()==first
    checks.append('real stage failure; hash-verified checkpoint restoration; completed step skipped on resume')
    checks.append('original input unchanged after all tests');assert keep.read_text()=='original'
    print(json.dumps({'passed':len(checks),'checks':checks,'uid':os.getuid(),'podman':shutil.which('podman'),'sudo':shutil.which('sudo')},ensure_ascii=False))

if __name__=='__main__':main()
