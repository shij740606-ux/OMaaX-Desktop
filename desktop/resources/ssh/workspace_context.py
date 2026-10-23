"""Project/session filing without moving existing user or legacy task data."""
import hashlib,json,shutil,time
from pathlib import Path

def normalize(value):
    value=value or {}
    if not isinstance(value,dict) or set(value)-{'projectId','projectName','sessionId','sessionName'}:raise ValueError('项目与对话信息不正确')
    defaults={'projectId':'unassigned','projectName':'未归档项目','sessionId':'manual','sessionName':'页面提交'}
    result={**defaults,**value}
    if any(not isinstance(v,str) or not v or len(v)>512 or any(c in v for c in ('\0','\n','\r')) for v in result.values()):raise ValueError('项目与对话信息不正确')
    return result

def key(value):return hashlib.sha256(value.encode()).hexdigest()[:24]

def base(r,value):
    import server_workspace as storage
    ctx=normalize(value);relative='projects/'+key(ctx['projectId'])+'/sessions/'+key(ctx['sessionId'])
    path=storage.managed(r,relative,existing=False)
    import protected_paths
    if not path.exists():protected_paths.check(r,path,'mkdir')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name in ('configs','references','files','tasks'):
        child=storage.managed(r,relative+'/'+name,existing=False)
        if not child.exists():protected_paths.check(r,child,'mkdir')
        child.mkdir(exist_ok=True,mode=0o700)
    db=r.database();db.execute('INSERT OR REPLACE INTO workspace_contexts VALUES(?,?,?,?,?)',(ctx['projectId'],ctx['sessionId'],json.dumps(ctx),relative,time.time()));db.commit()
    return path

def taskdir(r,spec):
    import server_workspace as storage
    if spec.get('context'):return base(r,spec['context'])/'tasks'/r.identifier(spec['id'])
    return storage.workspace(r)/'results'/r.identifier(spec['id'])

def listings(r):
    return [{'context':json.loads(row['metadata']),'path':row['path'],'updated':row['updated']} for row in r.database().execute('SELECT * FROM workspace_contexts ORDER BY updated DESC')]

def reserved(path):
    parts=path.parts
    return len(parts)<2 or parts[0]=='projects' and (len(parts)<=4 or len(parts)==5 and parts[-1] in ('configs','references','files','tasks'))

def relocate(r,source,destination):
    import server_workspace as storage
    root=storage.workspace(r)
    def saved(path):return path.relative_to(root).as_posix() if path==root or root in path.parents else str(path)
    old=saved(source);new=saved(destination)
    db=r.database();locations=[]
    for row in db.execute('SELECT id FROM jobs').fetchall():
        location=r.jobdir(row['id'])
        if location==source or source in location.parents:
            rel=saved(destination/location.relative_to(source))
            locations.append((row['id'],rel))
    cfg=r.config(db);changed=False
    for name,value in cfg.get('sources',{}).items():
        p=Path(value)
        if p==source or source in p.parents:cfg['sources'][name]=str(destination/p.relative_to(source));changed=True
    shutil.move(str(source),str(destination))
    try:
        db.executemany('INSERT OR REPLACE INTO job_locations VALUES(?,?)',locations)
        if changed:db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',('sources',json.dumps(cfg['sources'])))
        for row in db.execute('SELECT * FROM workspace_external_files').fetchall():
            p=Path(row['path'])
            if p==source or source in p.parents:
                db.execute('DELETE FROM workspace_external_files WHERE path=?',(str(p),))
                db.execute('INSERT OR REPLACE INTO workspace_external_files VALUES(?,?,?)',(str(destination/p.relative_to(source)),row['context'],row['created']))
        r.audit(db,'workspace.moved',{'from':old,'to':new})
    except BaseException:
        db.rollback();shutil.move(str(destination),str(source));raise
