import importlib.util,json,os,sys,tempfile,unittest,shutil,zipfile
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('workflow_engine',HERE/'workflow_engine.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
sys.modules['workflow_engine']=w
sys.path.insert(0,str(HERE))
import workflow_environments as environments
spec=importlib.util.spec_from_file_location('runner',HERE/'personal_runner.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
spec=importlib.util.spec_from_file_location('adapter',HERE/'workflow_adapter.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
w.CATALOG=json.loads((HERE.parent/'workflows/catalog.json').read_text(encoding='utf-8'));w.ENGINE_HASH='frozen-test-engine';w.ADAPTER_SOURCE=(HERE/'workflow_adapter.py').read_text(encoding='utf-8')
IMAGE='sha256:'+'a'*64

class ContractTests(unittest.TestCase):
 def setUp(self):
  fixtures=Path(os.environ.get('OMAAX_TEST_ROOT',tempfile.gettempdir()))/'omaax-ws-tests';fixtures.mkdir(parents=True,exist_ok=True)
  self.base=Path(tempfile.mkdtemp(prefix='contract-',dir=fixtures));self.control=self.base/'control';self.control.mkdir();self.data=self.base/'data';self.data.mkdir();(self.data/'input.txt').write_text('raw input')
  (self.data/'samples.yaml').write_text(json.dumps({'groups':{'C1':'control','T1':'treated'},'compare':[['control','treated']]}))
  self.patches=[patch.object(r,'ROOT',self.control),patch.object(r,'root',lambda:self.control),patch.object(r.os,'getuid',return_value=1000,create=True),patch.object(r,'protection',return_value={'rootless':True}),patch.object(r,'launch'),patch.object(r,'disk_available',return_value=10**12),patch.object(r,'command',return_value=json.dumps({'python':'3.12'}))]
  for p in self.patches:p.start()
  probe={kind:{name:True for spec in list(environments.DEPENDENCIES.values())+list(w.STEP_SOFTWARE.values()) for name in spec.get(kind,[])} for kind in ['modules','tools','r','services','processEnvironments']};probe['tools']['Rscript']=True
  self.patches.append(patch.object(w,'software_probe',return_value=probe));self.patches[-1].start()
  r.dispatch({'action':'configure','body':{'sources':{'data':str(self.data)},'images':[IMAGE]}})
  shutil.copyfile(HERE.parent/'workflows/kernel.zip',w.bundle_path(r))
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
 def configure(self,flow):return w.dispatch(r,'workflow_environment',{'workflow':flow,'image':IMAGE})
 def request(self,flow):
  stage={'gene_family':'pep','rnaseq':'rnaseq_de','singlecell':'singlecell_analyze','spatial':'spatial_analyze','metagenome':'metagenome_summarize'}[flow]
  self.configure(flow)
  return {'cpu':1,'memoryMb':512,'timeoutSeconds':300,'workflow':flow,'source':'data','input':'.','steps':[stage],'parameters':{stage:{'config':'samples.yaml'}} if flow=='rnaseq' else {},'configs':{}}
 def make(self,flow='gene_family'):return w.dispatch(r,'workflow_plan',self.request(flow),actor='agent')
 def test_all_five_workflows_generate_fixed_plans(self):
  for flow in ['gene_family','rnaseq','singlecell','spatial','metagenome']:
   record=self.make(flow);self.assertEqual(record['plan']['workflow'],flow);self.assertEqual(record['plan']['validationMode'],'execution_only');self.assertNotIn('scientificValidation',record['plan']);self.assertEqual(record['plan']['parameterOrigin'],'model_proposal')
 def test_missing_software_prevents_a_false_ready_plan(self):
  body=self.request('rnaseq')
  with patch.object(w,'software_probe',return_value={'tools':{'Rscript':True},'r':{},'modules':{}}):
   with self.assertRaisesRegex(ValueError,'尚不能运行'):w.dispatch(r,'workflow_plan',body)
  self.assertEqual(r.database().execute('SELECT COUNT(*) FROM workflow_plans').fetchone()[0],0)
 def test_startup_failure_returns_reason_without_a_result_manifest(self):
  record=self.make('rnaseq');job=record['plan']['id'];w.dispatch(r,'workflow_confirm',{'id':job,'digest':record['digest']})
  db=r.database();db.execute("UPDATE jobs SET state='failed',detail='执行版本变化，请重新生成计划' WHERE id=?",(job,));db.commit()
  result=w.dispatch(r,'workflow_result',{'id':job});self.assertEqual(result['fileCount'],0);self.assertEqual(result['error'],'执行版本变化，请重新生成计划')
  db.execute("UPDATE jobs SET state='complete' WHERE id=?",(job,));db.commit()
  with self.assertRaisesRegex(ValueError,'结果记录缺失'):w.dispatch(r,'workflow_result',{'id':job})
 def test_cancelled_job_does_not_display_steps_as_still_running(self):
  record=self.make();job=record['plan']['id'];w.dispatch(r,'workflow_confirm',{'id':job,'digest':record['digest']})
  output=r.jobdir(job)/'output';output.mkdir(parents=True,exist_ok=True)
  snapshot={'steps':[{'id':'done','state':'complete'},{'id':'active','state':'running'},{'id':'later','state':'waiting'}]}
  path=output/'workflow-progress.json';path.write_text(json.dumps(snapshot));before=path.read_bytes()
  db=r.database();db.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(job,));db.commit()
  plan=next(p for p in w.dispatch(r,'workflow_plans',{}) if p['plan']['id']==job)
  self.assertEqual([p['state'] for p in plan['progress']],['complete','cancelled','cancelled'])
  self.assertEqual(path.read_bytes(),before)
 def test_result_pages_prioritize_reports_and_keep_every_file_accessible(self):
  record=self.make();job=record['plan']['id'];w.dispatch(r,'workflow_confirm',{'id':job,'digest':record['digest']})
  output=r.jobdir(job)/'output';output.mkdir(parents=True,exist_ok=True);files=[]
  for name in ['analysis/report/results/report.html','analysis/plots/results/volcano.png','analysis/count/results/counts.tsv']+['cache/db/'+str(i)+'.log' for i in range(205)]:
   path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic');files.append({'path':name,'size':path.stat().st_size,'sha256':w.file_hash(path)})
  (output/'workflow-result.json').write_text(json.dumps({'state':'complete','steps':[],'files':files[::-1]}))
  db=r.database();db.execute("UPDATE jobs SET state='complete' WHERE id=?",(job,));db.commit()
  first=w.dispatch(r,'workflow_result',{'id':job});self.assertEqual(first['fileCount'],208);self.assertEqual(first['nextOffset'],200)
  self.assertEqual([x['path'] for x in first['files'][:3]],[x['path'] for x in files[:3]])
  second=w.dispatch(r,'workflow_result',{'id':job,'offset':first['nextOffset']});self.assertIsNone(second['nextOffset'])
  self.assertEqual(len({f['path'] for f in first['files']+second['files']}),208)
  for bad in [-1,True,'200',999]:
   with self.assertRaises(ValueError):w.dispatch(r,'workflow_result',{'id':job,'offset':bad})
 def test_empty_optional_numeric_value_uses_source_default(self):
  step=next(s for s in w.flow_for('rnaseq')['nodes'] if s['id']=='rnaseq_wgcna')
  params,_=w.resolve_parameters(w.flow_for('rnaseq'),[step],{'parameters':{'rnaseq_wgcna':{'max_genes':''}}})
  self.assertNotEqual(params['rnaseq_wgcna'].get('max_genes'),'')
 def test_user_declared_parameters_are_forwarded_without_biological_policy(self):
  request=self.request('rnaseq');request['parameters']['rnaseq_de'].update(padj=.8,log2fc=.25,module='M4');record=w.dispatch(r,'workflow_plan',request)
  self.assertEqual(record['plan']['parameters']['rnaseq_de']['padj'],.8);self.assertNotIn('biologicalReplicate',json.dumps(record['plan']))
 def test_agent_cannot_approve_install_or_submit(self):
  for action in ['workflow_environment','workflow_bundle_status','workflow_confirm']:
   with self.assertRaises(ValueError):w.dispatch(r,action,{},actor='agent')
 def test_no_custom_command_image_or_environment(self):
  for key in ['argv','image','script','command','environment']:
   request=self.request('gene_family');request[key]='danger'
   with self.assertRaises(ValueError):w.dispatch(r,'workflow_plan',request)
 def test_cli_unknown_keys_and_shell_fragments_rejected(self):
  for value in ['ok; touch /tmp/other','$(whoami)']:
   request=self.request('gene_family');request['parameters']={'pep':{'fasta':value}}
   with self.assertRaises(ValueError):w.dispatch(r,'workflow_plan',request)
  request=self.request('gene_family');request['parameters']={'pep':{'unknown':'bad'}}
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_plan',request)
 def test_source_missing_step_and_other_flow_step_rejected(self):
  flow=w.flow_for('gene_family')
  for step in ['singlecell_analyze','synteny_plot_revise','unknown']:
   with self.assertRaises(ValueError):w.select_steps(flow,[step])
 def test_readiness_distinguishes_source_environment_and_unchecked(self):
  step=next(s for s in w.flow_for('rnaseq')['nodes'] if s['id']=='rnaseq_wgcna')
  self.assertEqual(w.readiness_for(step,None)['state'],'missing_environment')
  self.assertEqual(w.readiness_for(step,{'error':'offline'})['state'],'unchecked')
  missing=w.readiness_for(step,{'tools':{'Rscript':True},'r':{'DESeq2':True,'ggplot2':True,'pheatmap':True}})
  self.assertEqual(missing['state'],'missing_environment');self.assertEqual(missing['missing'],['R 包 WGCNA'])
  self.assertEqual(w.readiness_for(step,{'tools':{'Rscript':True},'r':{k:True for k in ['WGCNA','DESeq2','ggplot2','pheatmap']}})['state'],'ready')
  self.assertEqual(w.readiness_for(step|{'entryPresent':False},None)['state'],'missing_source')
 def test_readiness_checks_every_step_and_reuses_image_probe(self):
  self.configure('gene_family')
  with patch.object(w,'software_probe',return_value={'tools':{},'modules':{},'r':{}}) as probe:
   answer=w.dispatch(r,'workflow_readiness',{'workflow':'gene_family'})
   flow=w.flow_for('gene_family');self.assertEqual(len(answer['steps']),len(flow['nodes'])+len(flow['extraNodes']))
   self.assertEqual(answer['steps']['synteny_plot_revise']['state'],'missing_source');self.assertEqual(probe.call_count,1)
   self.assertEqual(r.database().execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
 def test_readiness_cannot_select_arbitrary_image_or_execute_code(self):
  for key in ['image','argv','command','script']:
   with self.assertRaises(ValueError):w.dispatch(r,'workflow_readiness',{'workflow':'rnaseq',key:'bad'})
 def test_wgcna_files_resolve_in_copied_input_and_cannot_escape(self):
  step=next(s for s in w.flow_for('rnaseq')['nodes'] if s['id']=='rnaseq_wgcna')
  plan={'parameters':{'rnaseq_wgcna':{'expression_matrix':'expression.tsv','sample_table':'samples.tsv'}},'prefix':'test','id':'case','resources':{'cpu':2}}
  cmd=a.command(step,plan,self.base/'kernel',self.data,self.base/'output',self.base/'config',str(self.data))
  self.assertEqual(cmd[cmd.index('--expression-matrix')+1],str(self.data/'expression.tsv'))
  self.assertEqual(cmd[cmd.index('--sample-table')+1],str(self.data/'samples.tsv'))
  with self.assertRaises(ValueError):a.value_path('../other.tsv','expression_matrix',self.data,self.base/'output',str(self.data))
  for key in ['gene2go','gene2ko','go_obo','hisat2_index']:
   self.assertEqual(a.value_path('test.tsv',key,self.data,self.base/'output',str(self.data)),str(self.data/'test.tsv'))
  self.assertEqual(a.value_path('a.tsv,b.tsv','de_files',self.data,self.base/'output',str(self.data)),str(self.data/'a.tsv')+','+str(self.data/'b.tsv'))
 def test_every_present_step_has_a_fixed_command_and_editable_parameter_contract(self):
  checked=[]
  for flow in w.CATALOG['workflows']:
   for step in flow['nodes']+flow['extraNodes']:
    if not step['entryPresent']:continue
    fields={arg['key']:arg for arg in step['arguments'] if not arg['managed']}
    values={key:('test.tsv' if arg['type']=='string' else ['test.tsv'] if arg['type']=='array' else True if arg['type']=='boolean' else 2) for key,arg in fields.items() if arg.get('required') and step.get('parameterDefaults',{}).get(key) in (None,'')}
    for key in values:
     if fields[key].get('choices'):values[key]=fields[key]['choices'][0]
    body={'parameters':{step['id']:values}}
    if step['kind']=='omics':body['configs']={step['id']:{key: (["test.tsv"] if step.get('configSchema',{}).get('properties',{}).get(key,{}).get('type')=='array' else 'test.tsv') for key in step['contract']['required_config']}}
    parameters,configs=w.resolve_parameters(flow,[step],body)
    # Source defaults can name reference caches; use explicit input-relative test paths.
    for key,value in parameters[step['id']].items():
     if isinstance(value,str) and (value.startswith('/') or key.endswith(('_dir','_file'))):parameters[step['id']][key]='test.tsv'
    plan={'parameters':parameters,'configs':configs,'prefix':'test','id':'testcase','resources':{'cpu':2},'steps':[step['id']]}
    config_dir=self.base/'configs'/step['id'];config_dir.mkdir(parents=True)
    workspace=self.base/'outputs'/step['id'];workspace.mkdir(parents=True)
    command=a.command(step,plan,self.base/'kernel',self.data,workspace,config_dir,str(self.data))
    self.assertIsInstance(command,list);self.assertTrue(command);self.assertNotIn('sh',command[:1]);self.assertNotIn('bash',command[:1])
    checked.append(step['id'])
  self.assertEqual(set(checked),{s['id'] for f in w.CATALOG['workflows'] for s in f['nodes']+f['extraNodes'] if s['entryPresent']})
 def test_enrichment_reads_current_task_differential_results(self):
  flow=w.flow_for('rnaseq');step=next(s for s in flow['nodes'] if s['id']=='rnaseq_go_enrich')
  workspace=self.base/'outputs';plan={'parameters':{'rnaseq_go_enrich':{}},'prefix':'test','id':'case','resources':{'cpu':2},'steps':['rnaseq_de','rnaseq_go_enrich']}
  cmd=a.command(step,plan,self.base/'kernel',self.data,workspace,self.base/'configs',str(self.data))
  self.assertEqual(cmd[cmd.index('--input')+1],str(workspace/'final'))
 def test_steps_follow_fixed_order(self):
  flow=w.flow_for('gene_family');steps=w.select_steps(flow,['motif','identify']);self.assertEqual([s['id'] for s in steps],['identify','motif'])
 def test_input_escape_and_link_rejected(self):
  request=self.request('gene_family');request['input']='../data'
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_plan',request)
 def test_plan_and_code_fingerprint_protect_confirmation(self):
  record=self.make();body={'id':record['plan']['id'],'digest':record['digest']}
  with patch.object(w,'ENGINE_HASH','changed'):
   with self.assertRaises(ValueError):w.dispatch(r,'workflow_confirm',body)
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_confirm',body|{'digest':'wrong'})
 def test_input_changed_after_plan_is_rejected_even_same_size(self):
  record=self.make();p=self.data/'input.txt';s=p.stat();p.write_text('raw other');os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns))
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_confirm',{'id':record['plan']['id'],'digest':record['digest']})
 def test_unconfirmed_and_modified_task_specs_rejected(self):
  record=self.make();spec=w.job_spec(record['plan'])
  with self.assertRaises(ValueError):w.validate_bound_job(r,spec)
  w.dispatch(r,'workflow_confirm',{'id':record['plan']['id'],'digest':record['digest']});w.validate_bound_job(r,spec)
  spec['argv']=['sh','-c','other']
  with self.assertRaises(ValueError):w.validate_bound_job(r,spec)
 def test_confirmation_is_idempotent(self):
  record=self.make();body={'id':record['plan']['id'],'digest':record['digest']};w.dispatch(r,'workflow_confirm',body);w.dispatch(r,'workflow_confirm',body)
  self.assertEqual(r.database().execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
 def test_large_source_bundle_never_enters_argv(self):
  record=self.make();spec=w.job_spec(record['plan']);self.assertTrue(all(len(s)<8192 for s in spec['argv']));self.assertLess(len(json.dumps(spec)),10000)
 def test_results_need_settled_jobs_and_unchanged_files(self):
  record=self.make();job=record['plan']['id'];body={'id':job,'digest':record['digest']};w.dispatch(r,'workflow_confirm',body)
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_result',{'id':job})
  directory=r.jobdir(job)/'output';file=directory/'result.txt';file.write_text('result');m={'state':'complete','steps':[{'id':'pep','state':'complete'}],'files':[{'path':'result.txt','size':6,'sha256':w.file_hash(file)}]};(directory/'workflow-result.json').write_text(json.dumps(m));db=r.database();db.execute('UPDATE jobs SET state=?',('complete',));db.commit()
  self.assertEqual(w.dispatch(r,'workflow_result',{'id':job})['fileCount'],1);file.write_text('tamper')
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_result',{'id':job})
 def test_task_failure_never_becomes_success(self):
  record=self.make();job=record['plan']['id'];w.dispatch(r,'workflow_confirm',{'id':job,'digest':record['digest']});self.assertEqual(w.finish(r,job,True)[0],'failed')
 def test_nextflow_is_offline_pinned_and_has_no_nested_runtime(self):
  cmd=a.nextflow_command('/bin/nextflow',Path('/trusted'),['run','nf-core/scrnaseq','-r','wrong','-profile','docker','-params-file','/output/p.json','-work-dir','/output/nf'],Path('/output/fixed.config'))
  self.assertIn(str(Path('/trusted/upstream_sources/nf-core_scrnaseq_4.2.0')),cmd);self.assertIn('-offline',cmd);self.assertNotIn('docker',cmd);self.assertNotIn('wrong',cmd)
  with self.assertRaises(ValueError):a.nextflow_command('/bin/nf',Path('/trusted'),['run','unknown'],Path('/output/fixed.config'))
 def test_source_zip_entries_are_complete(self):
  with zipfile.ZipFile(HERE.parent/'workflows/kernel.zip') as z:
   self.assertIn('cli/run_omics.py',z.namelist());self.assertIn('upstream_sources/nf-core_scrnaseq_4.2.0/main.nf',z.namelist())
   for f in w.CATALOG['workflows']:
    for s in f['nodes']+f['extraNodes']:
     if s['entryPresent']:self.assertIn(s['entry'],z.namelist())
 def test_program_overrides_and_redirection_are_never_parameters(self):
  for value in ['--auto | sh','--auto > other','--auto & echo']:
   with self.assertRaises(ValueError):w.safe_values({'mafft_params':value})
  for key in ['r_script','python_executable','beforeScript']:
   with self.assertRaises(ValueError):w.safe_values({key:'other.py'})
  node=next(s for f in w.CATALOG['workflows'] for s in f['extraNodes'] if s['id']=='msa')
  self.assertTrue(next(a for a in node['arguments'] if a['key']=='r_script')['managed'])
 def test_candidate_family_executable_override_is_not_editable(self):
  flow=w.flow_for('rnaseq');step=next(s for s in flow['extraNodes'] if s['id']=='rnaseq_candidate_family')
  self.assertTrue(next(arg for arg in step['arguments'] if arg['key']=='interproscan')['managed'])
  with self.assertRaises(ValueError):w.resolve_parameters(flow,[step],{'parameters':{step['id']:{'interproscan':'bash'}}})
 def test_report_uses_selected_steps_without_independent_model(self):
  flow=w.flow_for('gene_family');steps=w.select_steps(flow,['phylogeny','gene_family_report'])
  values,_=w.resolve_parameters(flow,steps,{})
  self.assertEqual(values['gene_family_report']['expected_stages'],'phylogeny');self.assertTrue(values['gene_family_report']['no_model'])
 def test_adapter_snapshot_is_bound_to_confirmed_plan(self):
  record=self.make();plan=record['plan'];cfg=r.config(r.database())
  snapshot=Path(cfg['sources'][plan['planSource']]);(snapshot/'adapter.py').write_text('changed')
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_confirm',{'id':plan['id'],'digest':record['digest']})
 def test_declared_defaults_have_valid_types(self):
  for flow in w.CATALOG['workflows']:
   for node in flow['nodes']+flow['extraNodes']:
    for field in node['arguments']:
     if not field['managed'] and field.get('default') is not None:w.typed(field['default'],field)
 def test_inspection_is_read_only_and_folder_scoped(self):
  path=self.data/'samples.csv';path.write_text('sample,group\nS1,A\n')
  result=w.dispatch(r,'workflow_inspect',{'source':'data','input':'.'},actor='agent')
  self.assertTrue(result['readOnly']);self.assertEqual(result['headers']['samples.csv'],'sample,group');self.assertEqual(path.read_text(),'sample,group\nS1,A\n')
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_inspect',{'source':'data','input':'../other'},actor='agent')
 def test_paths_to_results_cannot_escape(self):
  for name in ['/etc/passwd','../other']:
   with self.assertRaises(ValueError):a.contained(self.base,name)
 @unittest.skipUnless(os.name=='posix','Linux no-follow directory descriptors')
 def test_copy_preserves_original_and_rejects_links(self):
  dest=self.base/'copied.txt';a.copy_input(self.data,'input.txt',dest,w.signature(self.data/'input.txt'));self.assertEqual(dest.read_text(),'raw input');self.assertEqual((self.data/'input.txt').read_text(),'raw input')
  (self.data/'linked.txt').symlink_to(self.data/'input.txt')
  with self.assertRaises(OSError):a.copy_input(self.data,'linked.txt',self.base/'other',w.signature(self.data/'input.txt'))

 def test_all_five_workflows_accept_absolute_input_without_registration(self):
  for flow in ['gene_family','rnaseq','singlecell','spatial','metagenome']:
   body=self.request(flow);body.pop('source');body['input']=self.data.as_posix()
   record=w.dispatch(r,'workflow_plan',body);self.assertEqual(record['plan']['sourceBase'],str(self.data));self.assertEqual(record['plan']['input'],'.')
 def test_server_reads_other_data_paths_and_never_writes_them(self):
  import server_workspace as storage
  ref=self.base/'reference';ref.mkdir();file=ref/'genome.fa';file.write_text('>chr1\nACGT\n');before=file.read_bytes()
  self.assertEqual(storage.files(r,{'operation':'read','path':str(file)})['text'],before.decode())
  self.assertEqual(storage.files(r,{'operation':'list','path':str(ref)})['fileCount'],1)
  with self.assertRaises(ValueError):storage.manage(r,{'operation':'delete','path':str(file)})
  with self.assertRaises(ValueError):storage.manage(r,{'operation':'delete','path':'../reference/genome.fa'})
  self.assertEqual(file.read_bytes(),before)
 def test_configs_and_tables_are_created_in_workspace_without_overwrite(self):
  import server_workspace as storage
  result=storage.write_config(r,{'path':'configs/samples.yaml','content':{'samples':{'S1':{'group':'A','r1':str(self.data/'input.txt')}}}})
  self.assertTrue(Path(result['path']).is_file())
  with self.assertRaises(FileExistsError):storage.write_config(r,{'path':'configs/samples.yaml','content':{}})
  result=storage.write_config(r,{'path':'configs/samples.tsv','format':'tsv','columns':['sample','group'],'content':[{'sample':'S1','group':'A'}]})
  self.assertEqual(Path(result['path']).read_text(),'sample\tgroup\nS1\tA\n')
 def test_reference_files_and_index_prefixes_are_exact_readonly_mounts(self):
  ref=self.base/'reference';ref.mkdir();fasta=ref/'genome.fa';fasta.write_text('>chr1\nACGT\n')
  for suffix in ['1.ht2','2.ht2']:(ref/('genome.'+suffix)).write_text('index')
  mounts,mapping,docs=w.external_inputs(r,{'fasta':fasta.as_posix(),'hisat2_index':(ref/'genome').as_posix()},{},self.data,IMAGE)
  self.assertEqual(len(mounts),3);self.assertTrue(all(m['target'].startswith('/input/refs/') for m in mounts));self.assertEqual(docs,{})
  record=self.request('gene_family');record['parameters']={'pep':{'fasta':fasta.as_posix()}};plan=w.dispatch(r,'workflow_plan',record)
  w.dispatch(r,'workflow_confirm',{'id':plan['plan']['id'],'digest':plan['digest']})
  spec=w.job_spec(plan['plan']);cfg=r.config(r.database())
  if os.name=='posix':args=r.container_args(spec,cfg);self.assertTrue(any(str(fasta) in part and part.endswith(':ro') for part in args))
  else:
   spec=json.loads(json.dumps(spec));spec['files'][0]['source']='/fixtures/genome.fa'
   with patch.object(r,'source_path',return_value=Path('/fixtures/input')):args=r.container_args(spec,cfg)
   self.assertTrue(any('/fixtures/genome.fa' in part and part.endswith(':ro') for part in args))
  self.assertTrue(any('/output:rw' in part for part in args))
  fasta.write_text('>chr1\nTGCA\n')
  with self.assertRaises(ValueError):w.validate_bound_job(r,w.job_spec(plan['plan']))
 def test_external_config_document_rewrites_paths_on_every_step(self):
  import server_workspace as storage
  a.PATH_MAP.clear();config=storage.write_config(r,{'path':'configs/samples.yaml','content':{'samples':{'S1':{'r1':self.data.joinpath('input.txt').as_posix(),'group':'A'}}}})
  mounts,mapping,docs=w.external_inputs(r,{'config':Path(config['path']).as_posix()},{},self.data,IMAGE)
  self.assertEqual(len(docs),1);self.assertEqual(len(mounts),1)
  configs=self.base/'derived';configs.mkdir();workspace=self.base/'task';workspace.mkdir();copied=self.base/'copied';copied.mkdir()
  plan={'pathMap':mapping,'configDocuments':docs};a.PATH_MAP.update(mapping);a.prepare_documents(plan,configs,copied,workspace,str(self.data),True)
  target=a.PATH_MAP[str(Path(config['path']))];self.assertEqual(json.loads(Path(target).read_text())['samples']['S1']['r1'],str(copied/'input.txt'))
  a.PATH_MAP.clear();a.PATH_MAP.update(mapping);a.prepare_documents(plan,configs,copied,workspace,str(self.data),False)
  self.assertEqual(a.PATH_MAP[str(Path(config['path']))],target);self.assertEqual(a.value_path(Path(config['path']).as_posix(),'config',copied,workspace,str(self.data)),target)
 def test_external_tables_with_paths_are_rewritten_as_derived_tables(self):
  import server_workspace as storage
  table=storage.write_config(r,{'path':'configs/reads.csv','format':'csv','columns':['sample','path'],'content':[{'sample':'S1','path':self.data.joinpath('input.txt').as_posix()}]})
  mounts,mapping,docs=w.external_inputs(r,{}, {'singlecell_prepare':{'samples':Path(table['path']).as_posix()}},self.data,IMAGE)
  configs=self.base/'derived';configs.mkdir();copied=self.base/'copied';copied.mkdir();a.PATH_MAP.clear();a.PATH_MAP.update(mapping)
  a.prepare_documents({'configDocuments':docs},configs,copied,self.base,str(self.data),True)
  self.assertIn(str(copied/'input.txt'),Path(a.PATH_MAP[str(Path(table['path']))]).read_text())
 def test_derived_document_mapping_resolves_other_config_documents_before_writing(self):
  configs=self.base/'derived';configs.mkdir();one=(self.base/'one.yaml').as_posix();two=(self.base/'two.yaml').as_posix();a.PATH_MAP.clear()
  a.prepare_documents({'configDocuments':{one:{'config':two},two:{'sample':'S1'}}},configs,self.data,self.base,str(self.data),True)
  self.assertEqual(json.loads(Path(a.PATH_MAP[one]).read_text())['config'],a.PATH_MAP[two])
 def test_workspace_delete_restore_and_active_job_protection(self):
  import server_workspace as storage
  file=Path(storage.write_config(r,{'path':'configs/safe.json','format':'json','content':{'sample':'A'}})['path'])
  deleted=storage.manage(r,{'operation':'delete','path':'configs/safe.json'});self.assertFalse(file.exists())
  self.assertEqual(storage.manage(r,{'operation':'trash'})['files'][0]['trashId'],deleted['trashId'])
  storage.restore(r,{'trashId':deleted['trashId']});self.assertEqual(json.loads(file.read_text()),{'sample':'A'})
  job=r.dispatch({'action':'submit','body':{'id':'active-job','image':IMAGE,'argv':['python3','--version'],'sources':[],'files':[{'source':str(file),'target':'/input/refs/'+'b'*20+'/safe.json'}],'cpu':1,'memoryMb':128,'timeoutSeconds':10}})
  with self.assertRaisesRegex(ValueError,'正在使用'):storage.manage(r,{'operation':'delete','path':'configs/safe.json'})
  with self.assertRaisesRegex(ValueError,'正在使用'):storage.manage(r,{'operation':'delete','path':'results/'+job['id']})
 def test_generated_plugin_scripts_and_indexes_use_protected_output(self):
  import server_workspace as storage
  script=storage.script(r,{'path':'files/analyze.py','content':'print("ok")'})['path']
  job=storage.analysis(r,{'inputs':[script,str(self.data/'input.txt')],'argv':['python3',script,str(self.data/'input.txt')],'cpu':1,'memoryMb':128,'timeoutSeconds':10})
  spec=json.loads(r.database().execute('SELECT spec FROM jobs WHERE id=?',(job['id'],)).fetchone()['spec'])
  self.assertTrue(str(r.ROOT/'workspace'/'results') in job['output']);self.assertTrue(all(v['target'].startswith('/input/refs/') for v in spec['files']));self.assertNotIn(script,spec['argv'])
  self.configure('rnaseq');fasta=self.base/'genome.fa';fasta.write_text('>chr1\nACGT\n');indexed=storage.index(r,{'kind':'hisat2','fasta':str(fasta),'cpu':1,'memoryMb':128,'timeoutSeconds':10})
  spec=json.loads(r.database().execute('SELECT spec FROM jobs WHERE id=?',(indexed['id'],)).fetchone()['spec']);self.assertEqual(spec['argv'][-1],'/output/genome');self.assertEqual(spec['files'][0]['source'],str(fasta))
 def test_new_jobs_use_workspace_while_old_job_paths_remain_readable(self):
  self.assertEqual(r.jobdir('new-job'),r.ROOT/'workspace/results/new-job')
  old=r.ROOT/'jobs/old-job';old.mkdir(parents=True);self.assertEqual(r.jobdir('old-job'),old)
 @unittest.skipUnless(os.name=='posix','Linux symlink ownership and no-follow paths')
 def test_trash_renames_links_without_touching_external_targets(self):
  import server_workspace as storage
  base=storage.workspace(r);link=base/'files/link';link.symlink_to(self.data/'input.txt')
  deleted=storage.manage(r,{'operation':'delete','path':'files/link'});self.assertEqual((self.data/'input.txt').read_text(),'raw input')
  storage.restore(r,{'trashId':deleted['trashId']});self.assertTrue(link.is_symlink())
  linked=base/'files/redirect';linked.symlink_to(self.data,target_is_directory=True)
  with self.assertRaises(ValueError):storage.manage(r,{'operation':'delete','path':'files/redirect/input.txt'})

if __name__=='__main__':unittest.main()

