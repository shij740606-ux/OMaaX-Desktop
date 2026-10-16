import json,os,unittest
from pathlib import Path
from unittest.mock import patch
import workflow_engine_test as fixture
import server_workspace as storage
import workspace_context as context
import server_file_ops as writes
r,w,a=fixture.r,fixture.w,fixture.a
C1={'projectId':'project-a','projectName':'项目甲','sessionId':'dialogue-a','sessionName':'第一次分析'}
C2={'projectId':'project-b','projectName':'项目乙','sessionId':'dialogue-b','sessionName':'第二次分析'}

class ProjectWorkspaceTests(unittest.TestCase):
 setUp=fixture.ContractTests.setUp
 tearDown=fixture.ContractTests.tearDown
 configure=fixture.ContractTests.configure
 request=fixture.ContractTests.request
 def call(self,action,body,actor='agent'):return r.dispatch({'action':action,'body':body,'actor':actor})
 def settle(self,job):
  db=r.database();db.execute("UPDATE jobs SET state='succeeded' WHERE id=?",(job,));db.commit()
 def test_same_name_configs_are_isolated_by_project_and_conversation(self):
  paths=[storage.write_config(r,{'path':'configs/samples.json','content':{'case':i},'context':ctx})['path'] for i,ctx in enumerate([C1,C2])]
  self.assertNotEqual(paths[0],paths[1]);self.assertEqual([json.loads(Path(p).read_text())['case'] for p in paths],[0,1])
  self.assertEqual(len(context.listings(r)),2)
  C3={**C1,'sessionId':'dialogue-c'};self.assertNotEqual(context.base(r,C1),context.base(r,C3))
 def test_all_five_plans_and_tasks_keep_context_and_requested_defaults(self):
  for flow in ['gene_family','rnaseq','singlecell','spatial','metagenome']:
   body=self.request(flow);body.update(context=C1);body.pop('cpu');body.pop('memoryMb');body.pop('timeoutSeconds')
   with patch.object(w.os,'cpu_count',return_value=64):plan=w.dispatch(r,'workflow_plan',body)
   self.assertEqual(plan['plan']['resources'],{'cpu':20,'memoryMb':51200,'timeoutSeconds':86400})
   spec=w.job_spec(plan['plan']);self.assertEqual(spec['context'],C1)
   with patch.object(w.os,'cpu_count',return_value=64):w.dispatch(r,'workflow_confirm',{'id':plan['plan']['id'],'digest':plan['digest']})
   directory=r.jobdir(plan['plan']['id']);self.assertEqual(directory.parent,context.base(r,C1)/'tasks')
   self.assertEqual(json.loads((directory/'task.json').read_text())['context'],C1)
 def test_plugin_and_reference_index_tasks_are_filed_by_context(self):
  script=storage.script(r,{'path':'files/tool.py','content':'print(1)','context':C1})['path']
  job=storage.analysis(r,{'inputs':[script],'argv':['python3',script],'context':C1,'cpu':1,'memoryMb':128,'timeoutSeconds':10})
  self.assertEqual(r.jobdir(job['id']).parent,context.base(r,C1)/'tasks')
  self.configure('rnaseq');fa=self.base/'genome.fa';fa.write_text('>chr1\nACGT\n')
  job=storage.index(r,{'fasta':str(fa),'context':C2,'cpu':1,'memoryMb':128,'timeoutSeconds':10})
  self.assertEqual(r.jobdir(job['id']).parent,context.base(r,C2)/'tasks')
 def test_move_task_keeps_result_export_and_log_location(self):
  job=storage.analysis(r,{'argv':['python3','--version'],'context':C1,'cpu':1,'memoryMb':128,'timeoutSeconds':10})['id']
  directory=r.jobdir(job);(directory/'output/result.txt').write_text('result');(directory/'log.txt').write_text('task log')
  with self.assertRaisesRegex(ValueError,'正在使用'):storage.manage(r,{'operation':'rename','path':directory.relative_to(storage.workspace(r)).as_posix(),'destination':'files/new-task'})
  self.settle(job);old=directory.relative_to(storage.workspace(r)).as_posix();destination=(context.base(r,C2)/'tasks/我的分析').relative_to(storage.workspace(r)).as_posix()
  storage.manage(r,{'operation':'move','path':old,'destination':destination})
  self.assertEqual(r.jobdir(job),storage.workspace(r)/destination)
  self.assertEqual(self.call('export',{'job':job,'paths':['result.txt']})[0]['size'],6)
  self.assertEqual((r.jobdir(job)/'log.txt').read_text(),'task log')
  pending=storage.manage(r,{'operation':'delete','path':destination});self.assertTrue(pending['requiresApproval']);row=self.call('server_delete_requests',{},'user')[0];deleted=self.call('server_delete_confirm',{'id':row['id'],'digest':row['digest']},'user');storage.restore(r,{'trashId':deleted['trashId']})
  self.assertEqual((r.jobdir(job)/'output/result.txt').read_text(),'result')
 def test_structural_roots_traversal_and_outside_deletion_are_rejected(self):
  base=context.base(r,C1);rel=base.relative_to(storage.workspace(r)).as_posix()
  for value in ['files',rel,rel+'/tasks',str(self.data), '../other']:
   with self.assertRaises(ValueError):storage.manage(r,{'operation':'delete','path':value})
  self.assertEqual((self.data/'input.txt').read_text(),'raw input')
 def test_task_result_and_log_entrypoints_are_not_detached_from_the_task(self):
  job=storage.analysis(r,{'argv':['python3','--version'],'context':C1,'cpu':1,'memoryMb':128,'timeoutSeconds':10})['id'];self.settle(job)
  directory=r.jobdir(job);(directory/'log.txt').write_text('log')
  for name in ['output','log.txt','task.json']:
   with self.assertRaisesRegex(ValueError,'整个任务文件夹'):storage.manage(r,{'operation':'rename','path':(directory/name).relative_to(storage.workspace(r)).as_posix(),'destination':'files/detached'})
  (directory/'output/result.txt').write_text('result');storage.manage(r,{'operation':'rename','path':(directory/'output/result.txt').relative_to(storage.workspace(r)).as_posix(),'destination':(directory/'output/renamed.txt').relative_to(storage.workspace(r)).as_posix()})
  self.assertEqual((directory/'output/renamed.txt').read_text(),'result')
 def test_search_and_file_directory_operations(self):
  storage.manage(r,{'operation':'mkdir','path':'files/new','context':C1})
  storage.write_config(r,{'path':'files/new/设计.json','content':{'group':'A'},'context':C1})
  rows=storage.manage(r,{'operation':'search','path':'files','context':C1,'query':'设计'})['files'];self.assertEqual(len(rows),1)
  storage.manage(r,{'operation':'rename','path':'files/new/设计.json','destination':'files/new/新设计.json','context':C1})
  self.assertTrue((context.base(r,C1)/'files/new/新设计.json').exists())
 def test_new_write_anywhere_and_binary_copy_preserve_source(self):
  output=self.base/'outside/new/config.json';value=self.call('server_write',{'path':str(output),'content':{'sample':'A'},'context':C1})
  self.assertTrue(value['created']);self.assertEqual(json.loads(output.read_text()),{'sample':'A'})
  source=self.base/'binary.bin';source.write_bytes(b'\x00\xffdata');target=self.base/'outside/copy.bin'
  self.call('server_copy',{'source':str(source),'path':str(target),'context':C1});self.assertEqual(target.read_bytes(),source.read_bytes())
  listed=storage.manage(r,{'operation':'external','context':C1})['files'];self.assertEqual(len(listed),2)
 @unittest.skipIf(os.name=='nt','POSIX descriptor identity is validated on the actual Linux server')
 def test_existing_file_needs_explicit_user_approval_and_backup(self):
  target=self.data/'input.txt';pending=self.call('server_write',{'path':str(target),'content':'revised','context':C1})
  self.assertTrue(pending['requiresApproval']);self.assertEqual(target.read_text(),'raw input')
  row=self.call('server_write_requests',{},'user')[0]
  with self.assertRaises(ValueError):self.call('server_write_confirm',{'id':row['id'],'digest':row['digest']})
  result=self.call('server_write_confirm',{'id':row['id'],'digest':row['digest']},'user')
  self.assertEqual(target.read_text(),'revised');self.assertEqual(Path(result['backup']).read_text(),'raw input')
  self.assertIn(context.key(C1['sessionId']),result['backup'])
 def test_changed_target_or_payload_prevents_overwrite(self):
  target=self.data/'input.txt';self.call('server_write',{'path':str(target),'content':'replacement'})
  row=self.call('server_write_requests',{},'user')[0];target.write_text('other edit')
  with self.assertRaisesRegex(ValueError,'已变化'):self.call('server_write_confirm',{'id':row['id'],'digest':row['digest']},'user')
  self.assertEqual(target.read_text(),'other edit')
  folder,item=writes.record(r,row['id']);(folder/'proposed.txt').write_text('tampered')
  with self.assertRaisesRegex(ValueError,'内容已变化'):self.call('server_write_confirm',{'id':row['id'],'digest':row['digest']},'user')
 def test_copy_source_change_and_rejection_leave_destination_untouched(self):
  source=self.base/'source.bin';source.write_bytes(b'original');target=self.data/'input.txt'
  self.call('server_copy',{'source':str(source),'path':str(target)});row=self.call('server_write_requests',{},'user')[0];source.write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'源文件已变化'):self.call('server_write_confirm',{'id':row['id'],'digest':row['digest']},'user')
  self.call('server_write_reject',{'id':row['id']},'user');self.assertEqual(self.call('server_write_requests',{},'user'),[])
  self.assertEqual(target.read_text(),'raw input')
 def test_credentials_control_and_invalid_context_cannot_be_written(self):
  for path in [r.ROOT/'state.sqlite',Path.home()/'.ssh/config']:
   with self.assertRaises(ValueError):self.call('server_write',{'path':str(path),'content':'bad'})
  with self.assertRaises(ValueError):self.call('server_write',{'path':str(self.base/'new'),'content':'bad','context':{'command':'bad'}})
 def test_strandedness_only_changes_derived_config_and_survives_steps(self):
  content={'samples':{'S1':{'r1':(self.data/'input.txt').as_posix(),'library_strandness':'non_stranded'}}}
  source=storage.write_config(r,{'path':'configs/design.json','content':content})['path'];original=Path(source).read_bytes()
  configs=self.base/'derived';configs.mkdir();a.PATH_MAP.clear()
  plan={'preferences':{'library_strandness':'reverse'},'configDocuments':{source:content}}
  a.prepare_documents(plan,configs,self.data,self.base,self.data.as_posix(),True)
  target=Path(a.PATH_MAP[source]);derived=json.loads(target.read_text());self.assertEqual(derived['rnaseq']['library_strandness'],'reverse');self.assertEqual(derived['samples']['S1']['library_strandness'],'reverse')
  a.PATH_MAP.clear();a.prepare_documents(plan,configs,self.data,self.base,self.data.as_posix(),False)
  self.assertEqual(a.PATH_MAP[source],str(target));self.assertEqual(Path(source).read_bytes(),original)

if __name__=='__main__':unittest.main()
