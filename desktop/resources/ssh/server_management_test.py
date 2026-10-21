import importlib.util,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import server_workspace as storage
import server_delete as deletion
import server_setup as setup
import workspace_context

spec=importlib.util.spec_from_file_location('management_runner',Path(__file__).with_name('personal_runner.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
C={'projectId':'test-project','projectName':'临时测试项目','sessionId':'test-dialogue','sessionName':'临时测试对话'}

class ManagementTests(unittest.TestCase):
 def setUp(self):
  root=Path(os.environ.get('OMAAX_TEST_ROOT',tempfile.gettempdir()));root.mkdir(parents=True,exist_ok=True)
  self.temp=tempfile.TemporaryDirectory(prefix='server-management-',dir=root);self.base=Path(self.temp.name);self.control=self.base/'control';self.control.mkdir()
  self.patches=[patch.object(r,'ROOT',self.control),patch.object(r,'root',lambda:self.control),patch.object(r.os,'getuid',return_value=os.getuid() if hasattr(os,'getuid') else 1000,create=True)]
  for p in self.patches:p.start()
  self.connections=[];original=r.database
  def connect():
   db=original();self.connections.append(db);return db
  self.patches.append(patch.object(r,'database',side_effect=connect));self.patches[-1].start()
  r.database().close();self.data=self.base/'user-data';self.data.mkdir();(self.data/'original.txt').write_text('keep original')
 def tearDown(self):
  for db in self.connections:db.close()
  for p in reversed(self.patches):p.stop()
  # Only this test's fixed temporary directory is removed; no real user paths are used.
  self.temp.cleanup()
 def call(self,action,body=None,actor='agent'):return r.dispatch({'action':action,'body':body or {},'actor':actor})
 def confirm(self,pending):
  row=next(i for i in self.call('server_delete_requests',actor='user') if i['id']==pending['id'])
  return self.call('server_delete_confirm',{'id':row['id'],'digest':row['digest']},'user')
 def test_new_account_workspace_is_under_omaax_and_conversations_are_separate(self):
  home=self.base/'user-home';home.mkdir();app=home/'omaax';control=app/'.servers/server-a';control.mkdir(parents=True)
  with patch.object(r,'ROOT',control),patch.object(r,'DEFAULT_ROOT',control),patch.object(r,'APP_HOME',app),patch.object(r,'root',lambda:control):
   first=workspace_context.base(r,C);second=workspace_context.base(r,{**C,'sessionId':'second'})
   self.assertTrue(first.is_relative_to(app));self.assertNotEqual(first,second)
   self.assertTrue((first/'configs').is_dir());self.assertTrue((second/'tasks').is_dir())
 def test_connection_initialization_does_not_require_or_install_podman(self):
  with patch.object(r,'protection',side_effect=ValueError('no podman')),patch.object(r,'command') as command:
   info=self.call('initialize',actor='user');self.assertTrue(Path(info['workspace']).is_dir());self.assertEqual(info['setup']['state'],'idle');command.assert_not_called()
 def test_setup_start_is_idempotent_and_restart_respects_saved_completion(self):
  fake=types.SimpleNamespace(flock=Mock(),LOCK_EX=1,LOCK_NB=2)
  with patch.dict(sys.modules,{'fcntl':fake}),patch.object(setup.subprocess,'Popen') as launch,patch('user_runtime.manager',return_value=('conda','/site/bin/conda')):
   a=self.call('setup_start',actor='user');b=self.call('setup_start',actor='user');self.assertEqual(a['id'],b['id']);self.assertEqual(launch.call_count,1)
   setup.save(r,{**a,'state':'complete','execution':'direct-v1'});self.assertEqual(self.call('setup_start',actor='user')['state'],'complete');self.assertEqual(launch.call_count,1)
   self.call('setup_start',{'retry':True},'user');self.assertEqual(launch.call_count,2)
 def test_setup_failure_is_visible_and_does_not_remove_workspace(self):
  fake=types.SimpleNamespace(flock=Mock(),LOCK_EX=1,LOCK_NB=2);setup.save(r,{'id':'test','state':'running','updated':setup.time.time()})
  with patch.dict(sys.modules,{'fcntl':fake}),patch.object(r,'dispatch',side_effect=ValueError('registry unreachable')),patch.dict(os.environ,{},clear=False):setup.run(r,'test')
  self.assertEqual(setup.status(r)['state'],'failed');self.assertIn('registry unreachable',setup.status(r)['detail']);self.assertTrue(storage.workspace(r).is_dir())
 def test_upgrade_rechecks_old_setup_without_changing_saved_config(self):
  before=r.config(r.database());setup.save(r,{'state':'complete','updated':1,'detail':'old environment'})
  self.assertEqual(setup.status(r)['state'],'idle');self.assertEqual(r.config(r.database()),before)
 def test_small_and_empty_files_always_require_user_confirmation(self):
  for size in (0,7):
   p=self.data/('small-'+str(size));p.write_bytes(b'x'*size)
   pending=self.call('server_delete',{'path':str(p)})
   self.assertTrue(pending['requiresNotice']);self.assertEqual(pending['bytes'],size);self.assertEqual(pending['paths'],[str(p)]);self.assertTrue(p.exists())
   row=next(row for row in self.call('server_delete_requests',actor='user') if row['id']==pending['id'])
   with self.assertRaisesRegex(ValueError,'用户确认'):self.call('server_delete_confirm',{'id':row['id'],'digest':row['digest']})
   p.write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'已变化'):self.confirm(pending)
   self.assertEqual(p.read_bytes(),b'changed')
 def test_single_external_file_moves_to_trash_and_restores(self):
  p=self.data/'original.txt';result=self.confirm(self.call('server_delete',{'path':str(p)}))
  self.assertFalse(p.exists());self.assertTrue(result['recoverable']);self.call('workspace_restore',{'trashId':result['trashId']});self.assertEqual(p.read_text(),'keep original')
 def test_display_receipt_is_required_and_model_cannot_forge_it(self):
  p=self.data/'original.txt';pending=self.call('server_delete',{'path':str(p),'context':C})
  self.assertFalse(pending['requiresApproval']);self.assertTrue(p.exists())
  row=self.call('server_delete_requests',actor='user')[0]
  payload={'id':row['id'],'digest':row['digest']}
  with self.assertRaises(ValueError):self.call('server_delete_notified',payload)
  with self.assertRaises(ValueError):self.call('server_delete_notified',{**payload,'digest':'forged'},'user')
  self.assertTrue(p.exists())
  result=self.call('server_delete_notified',payload,'user');self.assertFalse(p.exists())
  saved=json.loads((deletion.folder(r)/(row['id']+'.json')).read_text())
  self.assertIn('notifiedAt',saved);self.assertEqual(saved['state'],'complete')
  with self.assertRaises(ValueError):self.call('server_delete_notified',payload,'user')
  self.call('workspace_restore',{'trashId':result['trashId']});self.assertEqual(p.read_text(),'keep original')
 def test_foundation_probe_chooses_smallest_approved_image_only(self):
  large='sha256:'+'a'*64;small='sha256:'+'b'*64;unapproved='sha256:'+'c'*64
  rows=[{'Id':large[7:],'Size':16_000_000_000},{'Id':small[7:],'Size':1_000_000_000},{'Id':unapproved[7:],'Size':1000}]
  with patch.object(r,'command',return_value=json.dumps(rows)):
   self.assertEqual(setup.probe_image(r,[large,small]),small)
 def test_environment_inventory_is_read_only_scoped_and_reports_missing_images_and_active_steps(self):
  installed='sha256:'+'a'*64;missing='sha256:'+'b'*64;stage='sha256:'+'c'*64;other='sha256:'+'d'*64
  cfg={'images':[installed,missing,stage],'workflowEnvironments':{'rnaseq':{'image':installed,'stageImages':{'rnaseq_qc':stage}}}}
  db=r.database()
  for key,value in cfg.items():db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',(key,json.dumps(value)))
  db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',('active',json.dumps({'image':installed,'pipeline':[{'image':stage}]}),'running',1,1,''));db.commit()
  rows=[{'Id':installed[7:],'Names':['localhost/omaax-workflow-rnaseq:1'],'Size':1234},{'id':stage[7:],'names':['localhost/omaax-auto-test:1'],'size':2000},{'Id':other[7:],'Names':['unrelated/service:1'],'Size':100}]
  before=r.config(db)
  with patch.object(setup.shutil,'which',return_value='/usr/bin/podman'),patch.object(r,'command',return_value=json.dumps(rows)) as command:
   result=self.call('server_environments',actor='user')
   self.assertEqual(command.call_count,1);self.assertEqual(command.call_args.args[0][:2],['podman','images'])
  items={item['id']:item for item in result['images']}
  self.assertEqual(set(items),{installed,missing,stage});self.assertFalse(items[missing]['installed']);self.assertEqual(items[installed]['size'],1234)
  self.assertEqual(items[stage]['activeJobs'],1);self.assertEqual(items[installed]['activeJobs'],1);self.assertEqual(items[stage]['bindings'][0]['steps'],['rnaseq_qc'])
  self.assertEqual(r.config(db),before);self.assertEqual((self.data/'original.txt').read_text(),'keep original')
 def test_conda_listing_remains_available_when_podman_is_missing_and_never_installs(self):
  home=self.base/'home';home.mkdir();prefix=home/'custom-env';(prefix/'conda-meta').mkdir(parents=True);(prefix/'conda-meta/history').write_text('unchanged')
  record=home/'.conda/environments.txt';record.parent.mkdir();record.write_text(str(prefix)+'\n'+str(prefix)+'\n'+str(home/'missing-env')+'\n');before=record.read_bytes()
  with patch.object(setup.Path,'home',return_value=home),patch.object(setup.shutil,'which',return_value=None),patch.object(r.user_runtime,'manager',return_value=None),patch.object(r,'command') as command:
   result=self.call('server_environments',{'conda':True},'user')
   self.assertEqual('',result['error']);self.assertEqual(result['conda']['environments'],[{'name':'custom-env','path':str(prefix)}]);command.assert_not_called()
  self.assertEqual(record.read_bytes(),before);self.assertEqual((prefix/'conda-meta/history').read_text(),'unchanged')
 def test_invalid_inventory_does_not_hide_the_separate_conda_result(self):
  home=self.base/'home';prefix=home/'miniconda3/envs/existing';(prefix/'conda-meta').mkdir(parents=True);(prefix/'conda-meta/history').write_text('keep')
  with patch.object(setup.Path,'home',return_value=home),patch.object(setup.shutil,'which',return_value='/usr/bin/podman'),patch.object(r,'command',return_value=json.dumps({'invalid':'images'})) as command:
   result=self.call('server_environments',{'conda':True},'user')
  self.assertIn('格式',result['error']);self.assertEqual(result['images'],[]);self.assertEqual(result['conda']['environments'],[{'name':'existing','path':str(prefix)}]);self.assertEqual(command.call_count,1)
 def test_environment_query_rejects_commands_and_non_boolean_options(self):
  with patch.object(r,'command') as command:
   for body in [{'conda':'true'},{'command':'anything'},{'path':'/other'}]:
    with self.assertRaisesRegex(ValueError,'参数不正确'):self.call('server_environments',body,'user')
   command.assert_not_called()
 def test_foundation_probe_keeps_isolation_limits_and_has_named_scope(self):
  with patch.object(r,'command',return_value='omaax-isolation-ready\n') as command:
   setup.verify_environment(r,'sha256:'+'a'*64)
   args=command.call_args.args[0]
   for value in ['--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--userns=keep-id','--memory=512m']:
    self.assertIn(value,args)
   self.assertEqual(command.call_args.kwargs['timeout'],180);self.assertTrue(args[args.index('--name')+1].startswith('omaax-envcheck-'))
 def test_probe_timeout_has_chinese_explanation_and_cannot_remove_other_containers(self):
  import subprocess
  calls=[]
  def command(args,**kwargs):
   calls.append(args)
   if args[:2]==['podman','run']:raise subprocess.TimeoutExpired(args,180)
   return json.dumps([{'Config':{'Labels':{'org.omaax.environment-check':'another-request'}}}])
  with patch.object(r,'command',side_effect=command):
   with self.assertRaisesRegex(setup.ProbeFailure,'SSH 登录正常'):setup.verify_environment(r,'sha256:'+'a'*64)
  self.assertFalse(any(args[:2]==['podman','rm'] for args in calls));self.assertTrue(list(self.control.glob('environment-probe-*.json')))
 def test_old_timeout_is_presented_readably_without_rewriting_history(self):
  original={'state':'failed','detail':"Command '['podman', 'run']' timed out after 60 seconds"};setup.save(r,original)
  shown=setup.status(r);self.assertIn('上次环境检查超时',shown['detail']);self.assertEqual(shown['errorDetail'],original['detail'])
  self.assertEqual(json.loads(setup.path(r).read_text(encoding='utf-8')),original)
 def test_recursive_deletion_warns_and_model_cannot_confirm(self):
  pending=self.call('server_delete',{'path':str(self.data),'recursive':True});self.assertTrue(pending['requiresNotice']);self.assertTrue(self.data.exists())
  row=self.call('server_delete_requests',actor='user')[0]
  with self.assertRaisesRegex(ValueError,'用户确认'):self.call('server_delete_confirm',{'id':row['id'],'digest':row['digest']})
  self.confirm(pending);self.assertFalse(self.data.exists())
 def test_wildcards_freeze_only_matched_files_and_leave_other_files(self):
  for name in ['one.tmp','two.tmp','keep.dat']:(self.data/name).write_text(name)
  pending=self.call('server_delete',{'path':str(self.data/'*.tmp')});self.assertEqual(len(pending['paths']),2);self.assertTrue((self.data/'one.tmp').exists())
  (self.data/'later.tmp').write_text('not in preview');self.confirm(pending)
  self.assertTrue((self.data/'later.tmp').exists());self.assertTrue((self.data/'keep.dat').exists());self.assertFalse((self.data/'one.tmp').exists())
 def test_changed_recursive_scope_rejects_confirmation_without_deleting(self):
  pending=self.call('server_delete',{'path':str(self.data),'recursive':True});(self.data/'new.txt').write_text('new')
  with self.assertRaisesRegex(ValueError,'已变化'):self.confirm(pending)
  self.assertTrue((self.data/'original.txt').exists())
 def test_over_10gb_metadata_requires_warning_without_allocating_large_data(self):
  p=self.data/'large.bin'
  p.write_bytes(b'small test fixture');summary=deletion.snapshot(r,[p]);summary['bytes']=10_000_000_001
  self.assertEqual(deletion.LIMIT,10_000_000_000)
  with patch.object(deletion,'snapshot',return_value=summary):pending=self.call('server_delete',{'path':str(p)})
  self.assertTrue(pending['requiresNotice']);self.assertIn('删除数据超过 10 GB',pending['warnings']);self.assertTrue(p.exists())
  row=self.call('server_delete_requests',actor='user')[0];self.call('server_delete_reject',{'id':row['id']},'user');self.assertTrue(p.exists())
 def test_credential_and_control_roots_and_active_inputs_are_protected(self):
  for p in [Path.home(),Path.home()/'.ssh',r.ROOT,storage.workspace(r),self.data/'../other']:
   with self.assertRaises(ValueError):self.call('server_delete',{'path':str(p),'recursive':True})
  with patch.object(storage,'busy_path',return_value=True):
   with self.assertRaisesRegex(ValueError,'正在使用'):self.call('server_delete',{'path':str(self.data/'original.txt')})
 def test_import_resume_reuses_destination_and_does_not_register_partial_data(self):
  body={'id':'management-transfer','bytes':10,'context':C}
  with patch.object(r,'disk_available',return_value=100):a=self.call('import_prepare',body,'user');b=self.call('import_prepare',body,'user')
  self.assertEqual(a,b);self.assertTrue(Path(a['path']).is_relative_to(workspace_context.base(r,C)))
  self.assertNotIn('import_management-transfer',r.config(r.database()).get('sources',{}))
 def test_cross_filesystem_delete_verifies_backup_and_restore(self):
  import errno
  p=self.data/'original.txt';lstat=Path.lstat;rename=Path.rename
  def cross_stat(value,*a,**kw):
   info=lstat(value,*a,**kw)
   if value==p:return types.SimpleNamespace(**{k:(info.st_dev+1 if k=='st_dev' else getattr(info,k)) for k in dir(info) if k.startswith('st_')})
   return info
  with patch.object(Path,'lstat',cross_stat):result=self.confirm(self.call('server_delete',{'path':str(p)}))
  self.assertFalse(p.exists());content=storage.workspace(r)/'.trash'/result['trashId']/'content';self.assertEqual(content.read_text(),'keep original')
  def cross_restore(value,dest):
   if value==content:raise OSError(errno.EXDEV,'simulated cross filesystem')
   return rename(value,dest)
  with patch.object(Path,'rename',cross_restore):self.call('workspace_restore',{'trashId':result['trashId']})
  self.assertEqual(p.read_text(),'keep original');self.assertFalse(content.exists())
 def test_cross_filesystem_without_backup_space_does_not_remove_source(self):
  p=self.data/'original.txt';lstat=Path.lstat
  def cross_stat(value,*a,**kw):
   info=lstat(value,*a,**kw)
   return types.SimpleNamespace(**{k:(info.st_dev+1 if k=='st_dev' else getattr(info,k)) for k in dir(info) if k.startswith('st_')}) if value==p else info
  with patch.object(Path,'lstat',cross_stat),patch.object(deletion.shutil,'disk_usage',return_value=types.SimpleNamespace(free=1)):
   with self.assertRaisesRegex(ValueError,'空间不足'):self.confirm(self.call('server_delete',{'path':str(p)}))
  self.assertEqual(p.read_text(),'keep original')

if __name__=='__main__':unittest.main()
