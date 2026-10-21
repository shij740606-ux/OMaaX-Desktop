"""Verify installer resources and unauthenticated native entrypoints without live servers."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
installer=Path(sys.argv[1]).resolve()
local_access='--local' in sys.argv
version=json.loads((ROOT/'desktop/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))['version']
assert installer.name==f'OMaaX_{version}_x64-setup.exe'
out=ROOT/('validation-local/auth-release-extracted-'+version);out.mkdir(exist_ok=True)
seven=r'C:\Program Files\7-Zip\7z.exe'
checks=[('dsh/node_modules/@biodsh/'+name+'/index.js','desktop/dsh-plugins/'+name+'/index.js') for name in ['auth-guard','workflow-guard','step-guard']]
checks.append(('dsh/node_modules/@biodsh/auth-guard/local.js','desktop/dsh-plugins/auth-guard/local.js'))
checks.append(('scripts/company-llm.mjs','desktop/resources/scripts/company-llm.mjs'))
checks.append(('dsh/node_modules/@biodsh/auth-guard/image.js','desktop/dsh-plugins/auth-guard/image.js'))
checks += [('ssh/'+name,'desktop/resources/ssh/'+name) for name in ['workflow_environments.py','workflow_engine.py','workflow-dependencies.json','personal_runner.py','conda_runtime.py','workflow_adapter.py','server_workspace.py','user_runtime.py','user_runtime.sh','server_setup.py','server_delete.py','server_file_ops.py','education.cjs','client.cjs','connection-pool.cjs','relay.cjs','stage-workflows.cjs']]
checks += [('scripts/download-skill.mjs','desktop/resources/scripts/download-skill.mjs'),('skills/catalog.json','desktop/resources/skills/catalog.json'),(f'omaax-desktop-{version}.ico','desktop/src-tauri/icons/icon.ico')]
subprocess.run([seven,'t',str(installer)],check=True,stdout=subprocess.DEVNULL)
subprocess.run([seven,'x','-y',str(installer),'-o'+str(out),'OMaaX.exe','education/*',*[x[0] for x in checks]],check=True,stdout=subprocess.DEVNULL)
checks += [('education/'+str(p.relative_to(ROOT/'desktop/resources/education')).replace('\\','/'),'desktop/resources/education/'+str(p.relative_to(ROOT/'desktop/resources/education')).replace('\\','/')) for p in (ROOT/'desktop/resources/education').rglob('*') if p.is_file()]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
resources=[]
for relative,source in checks:
 assert sha(out/relative)==sha(ROOT/source),relative
 resources.append({'file':relative,'sha256':sha(out/relative),'matchesSource':True})
env=dict(os.environ,OMAAX_ACCOUNT_HOME=str(ROOT/'validation-local/auth-package-empty-account'),BIODSH_HOME=str(ROOT/'validation-local/auth-package-empty-profile'))
exe=out/'OMaaX.exe'
# Tauri patches the runtime bundle marker to NSS for NSIS, then restores UNK in the build artifact.
release_bytes=(ROOT/'desktop/src-tauri/target/release/OMaaX.exe').read_bytes()
assert exe.read_bytes()==release_bytes.replace(b'__TAURI_BUNDLE_TYPE_VAR_UNK',b'__TAURI_BUNDLE_TYPE_VAR_NSS'),'Packaged executable differs beyond the NSIS bundle marker'
requests=[{'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'workflow_prepare','arguments':{}}},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'server_delete_confirm','arguments':{}}}]
r=subprocess.run([str(exe),'--ssh-mcp'],input=''.join(json.dumps(x)+'\n' for x in requests),capture_output=True,text=True,encoding='utf-8',env=env,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
rows=[json.loads(x) for x in r.stdout.splitlines() if x.startswith('{')]
assert r.returncode==0 and len(rows)==3 and [row.get('id') for row in rows]==[1,2,3],(r.returncode,rows,r.stderr)
names={t['name'] for t in rows[0]['result']['tools']}
analysis=next(t for t in rows[0]['result']['tools'] if t['name']=='workspace_analysis')
assert 'without sudo, Podman or mandatory isolation' in analysis['description'] and 'never forge receipts or perform deletion through a script or command' in analysis['description']
assert 'Every deletion' in next(t for t in rows[0]['result']['tools'] if t['name']=='server_delete')['description']
assert {'workflow_readiness','workflow_prepare','workflow_prepare_status','workflow_environment'}<=names
assert 'server_delete_confirm' not in names
assert all(row['error']['code']==-32001 for row in rows[1:]),rows
r=subprocess.run([str(exe),'--auth-check'],capture_output=True,text=True,encoding='utf-8',env=env,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
assert r.returncode==1 and json.loads(r.stdout)=={'ok':False}
if local_access:
 env['BIODSH_HOME']=env['OMAAX_ACCOUNT_HOME']
 r=subprocess.run([str(exe),'--auth-check'],capture_output=True,text=True,encoding='utf-8',env=env,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
 assert r.returncode==0 and json.loads(r.stdout)=={'ok':True,'userId':'local'}
 r=subprocess.run([str(exe),'--ssh-mcp'],input=json.dumps({'jsonrpc':'2.0','id':9,'method':'tools/call','params':{'name':'server_delete_confirm','arguments':{}}})+'\n',capture_output=True,text=True,encoding='utf-8',env=env,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
 row=json.loads(r.stdout);assert row.get('error') or row.get('result',{}).get('isError')
else:
 # Matching the legacy root must not accidentally enable a local-access build.
 env['BIODSH_HOME']=env['OMAAX_ACCOUNT_HOME']
 r=subprocess.run([str(exe),'--auth-check'],capture_output=True,text=True,encoding='utf-8',env=env,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
 assert r.returncode==1 and json.loads(r.stdout)=={'ok':False},'Release unexpectedly permits anonymous local access'
data={'version':version,'installer':installer.name,'bytes':installer.stat().st_size,'sha256':sha(installer),'executableSha256':sha(exe),'resourceChecks':resources,'packagedMcp':{'toolCount':len(names),'modelEnvironmentTools':True,'mismatchedProfileCallsDenied':True,'localAccess':local_access,'deleteConfirmationDenied':True,'realServersContacted':False},'installedApplicationReplaced':False,'realHubLoginVerified':False}
report=ROOT/('provenance/releases/'+version);report.mkdir(parents=True,exist_ok=True)
(report/'release-verification.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(data,ensure_ascii=False,indent=2))
