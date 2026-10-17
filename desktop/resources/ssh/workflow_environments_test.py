import ast,importlib.util,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).parent
sys.path.insert(0,str(HERE))
import workflow_engine as w
import workflow_environments as e
import personal_runner as r
w.CATALOG=json.loads((HERE.parent/'workflows/catalog.json').read_text(encoding='utf-8'))
IMAGE='sha256:'+'a'*64

class EnvironmentTests(unittest.TestCase):
 def setUp(self):
  parent=Path(os.environ.get('OMAAX_TEST_ROOT',tempfile.gettempdir()))/'omaax-env-tests';parent.mkdir(parents=True,exist_ok=True)
  self.base=Path(tempfile.mkdtemp(dir=parent));self.control=self.base/'control';self.control.mkdir()
  self.patches=[patch.object(r,'ROOT',self.control),patch.object(r,'root',return_value=self.control),patch.object(r,'protection',return_value={}),patch.object(r.os,'getuid',return_value=1000,create=True),patch.object(w,'ready_bundle',return_value=HERE.parent/'workflows/kernel.zip'),patch.object(w,'bundle_path',return_value=HERE.parent/'workflows/kernel.zip')]
  if sys.platform=='win32':self.patches.append(patch.dict(sys.modules,{'fcntl':types.SimpleNamespace(flock=lambda *args:None,LOCK_EX=2,LOCK_NB=4)}))
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
 def step(self,flow,sid):return next(s for s in w.flow_for(flow)['nodes']+w.flow_for(flow)['extraNodes'] if s['id']==sid)
 def probe(self,spec):return {kind:{name:True for name in names} for kind,names in spec.items()}
 def test_model_can_prepare_and_repair_but_not_confirm_execution(self):
  body={'workflow':'rnaseq','steps':['rnaseq_qc'],'recipes':{'rnaseq_qc':{'packages':['fastp=0.23.4'],'script':'echo repair'}}}
  with patch.object(e,'launch'):
   task=w.dispatch(r,'workflow_prepare',body,actor='agent')
   self.assertEqual(task['request']['recipes'],body['recipes'])
   e.update(r.database(),task['id'],'failed','download failed')
   body['recipes']['rnaseq_qc']['packages']=['fastp']
   retried=w.dispatch(r,'workflow_prepare',body,actor='agent')
   self.assertNotEqual(task['id'],retried['id'])
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_confirm',{},actor='agent')
 def test_launch_failure_is_logged_and_model_can_retry(self):
  body={'workflow':'rnaseq','steps':['rnaseq_qc']}
  with patch.object(e,'launch',side_effect=OSError('process resource temporarily unavailable')):
   task=w.dispatch(r,'workflow_prepare',body,actor='agent')
  self.assertEqual(task['state'],'failed')
  log=w.dispatch(r,'workflow_prepare_status',{'workflow':'rnaseq'},actor='agent')
  self.assertIn('process resource temporarily unavailable',log['log'])
  with patch.object(e,'launch'):
   retry=w.dispatch(r,'workflow_prepare',body,actor='agent')
  self.assertEqual(retry['state'],'queued');self.assertNotEqual(task['id'],retry['id'])
 def test_model_recipe_is_confined_to_build_context_and_validated(self):
  step=self.step('rnaseq','rnaseq_qc');directory=self.base/'recipe';directory.mkdir()
  recipe={'packages':['python=3.10','fastp'],'channels':['conda-forge','bioconda'],'script':'echo fixed > /tmp/check\nrm -rf /tmp/disposable'}
  e.validate_recipes({'rnaseq_qc':recipe},[step]);text=e.build_context(w,r,directory,step,e.requirements(step),recipe)
  self.assertIn('RUN /bin/bash -eu /opt/omaax/model-install.sh',text);self.assertIn('python=3.10',text);self.assertNotIn('python=3.12',text)
  self.assertNotIn(recipe['script'],text);self.assertEqual((directory/'model-install.sh').read_text(),recipe['script'])
  for bad in [{'other':recipe},{'rnaseq_qc':{'packages':['--mount=/home']}},{'rnaseq_qc':{'script':'a\x00b'}},{'rnaseq_qc':{'volumes':['/home']}}]:
   with self.assertRaises(ValueError):e.validate_recipes(bad,[step])
 def test_all_65_steps_have_dependencies_and_workflow_scope(self):
  for flow in w.CATALOG['workflows']:
   for step in flow['nodes']+flow['extraNodes']:
    self.assertIn(step['id'],e.DEPENDENCIES)
  wgcna=e.packages(e.requirements(self.step('rnaseq','rnaseq_wgcna')))
  self.assertIn('r-wgcna',wgcna);self.assertNotIn('mafft',wgcna);self.assertNotIn('scanpy',wgcna)
  for flow,sid,expected in [('gene_family','msa','mafft'),('singlecell','singlecell_analyze','scanpy=1.12.4'),('spatial','spatial_analyze','squidpy=1.8.3'),('metagenome','metagenome_differential','bioconductor-ancombc')]:self.assertIn(expected,e.packages(e.requirements(self.step(flow,sid))))
 def test_install_reuses_existing_image_and_does_not_build(self):
  sid='metagenome_summarize';spec=e.requirements(self.step('metagenome',sid))
  with patch.object(e,'launch') as launch,patch.object(w,'available_images',return_value={'metagenome':IMAGE}),patch.object(w,'software_probe',return_value=self.probe(spec)),patch.object(e,'build_context') as build:
   task=e.start(r,{'workflow':'metagenome','steps':[sid]});again=e.start(r,{'workflow':'metagenome','steps':[sid]})
   self.assertEqual(task['id'],again['id']);self.assertEqual(launch.call_count,1)
   e.run(r,task['id']);self.assertEqual(e.status(r,'metagenome')['state'],'complete');build.assert_not_called()
   cfg=r.config(r.database());self.assertEqual(cfg['workflowEnvironments']['metagenome']['stageImages'],{sid:IMAGE})
 def test_failed_install_keeps_existing_mapping(self):
  db=r.database();previous={'rnaseq':{'image':IMAGE,'stageImages':{'rnaseq_de':IMAGE}}};db.execute('INSERT INTO config VALUES(?,?)',('workflowEnvironments',json.dumps(previous)));db.commit()
  with patch.object(e,'launch'),patch.object(w,'available_images',return_value={}),patch.object(w,'software_probe',return_value={}),patch.object(r,'command',side_effect=ValueError('no image')),patch.object(e.subprocess,'run',return_value=types.SimpleNamespace(returncode=1)),patch.object(e,'prepare_rootfs'):
   task=e.start(r,{'workflow':'rnaseq','steps':['rnaseq_wgcna']});e.run(r,task['id'])
  self.assertEqual(e.status(r,'rnaseq')['state'],'failed');self.assertIn('软件安装失败',e.status(r,'rnaseq')['detail']);self.assertEqual(r.config(db)['workflowEnvironments'],previous)
 def test_r_check_timeout_does_not_trigger_reinstallation(self):
  probe={'tools':{'Rscript':True},'r':{},'rError':'R 软件检查超时'}
  with patch.object(e,'launch'),patch.object(w,'available_images',return_value={'methylseq':IMAGE}),patch.object(w,'software_probe',return_value=probe),patch.object(e,'build_context') as build:
   task=e.start(r,{'workflow':'methylseq','steps':['methylseq_dmr']});e.run(r,task['id'])
   self.assertEqual(e.status(r,'methylseq')['state'],'failed');self.assertIn('检查未完成',e.status(r,'methylseq')['detail']);build.assert_not_called()
 def test_source_missing_and_injected_requests_rejected(self):
  for request in [{'workflow':'gene_family','steps':['synteny_plot_revise']},{'workflow':'rnaseq','steps':['rnaseq_de'],'command':'bad'},{'workflow':'rnaseq','steps':['rnaseq_de'],'parameters':{'rnaseq_de':{'config':'a;bad'}}}]:
   with self.assertRaises(ValueError):e.start(r,request)
  with self.assertRaises(ValueError):w.dispatch(r,'workflow_prepare',{},actor='agent')
 def test_dynamic_choices_and_offline_services_are_not_success(self):
  qc=e.requirements(self.step('rnaseq','rnaseq_qc'),{'qc_tool':'cutadapt'});self.assertEqual(qc['tools'],['cutadapt'])
  spec=e.requirements(self.step('gene_family','pep'));self.assertTrue(spec['services'])
  upstream=self.step('metagenome','metagenome_profile');state=w.readiness_for(upstream,{'tools':{'nextflow':True,'java':True}},e.requirements(upstream));self.assertEqual(state['state'],'missing_environment')
 def test_recipe_has_no_user_data_and_only_step_dependencies(self):
  step=self.step('gene_family','msa');recipe=e.build_context(w,r,self.base,step,e.requirements(step))
  self.assertIn('mafft',recipe);self.assertNotIn('WGCNA',recipe);self.assertNotIn('VOLUME',recipe);self.assertNotIn('/home/',recipe)
  self.assertIn('--override-channels',recipe)
 def test_nextflow_stage_uses_its_pinned_process_environments(self):
  step=self.step('singlecell','singlecell_upstream');recipe=e.build_context(w,r,self.base,step,e.requirements(step))
  self.assertTrue(list((self.base/'upstream').rglob('environment.yml')))
  self.assertIn('prepare-nextflow.py',recipe)
  bootstrap=(self.base/'prepare-nextflow.py').read_text()
  self.assertNotIn('UPSTREAM_ID',bootstrap);self.assertIn(step['nextflowSource'],bootstrap)
 def test_parameter_branches_install_only_the_selected_tools(self):
  step=self.step('rnaseq','rnaseq_quant')
  count=e.requirements(step,{'quant_mode':'count_only'})
  self.assertNotIn('salmon',count['tools']);self.assertNotIn('kallisto',count['tools']);self.assertNotIn('tximport',count.get('r',[]))
  quant=e.requirements(step,{'abundance_engine':'kallisto'})
  self.assertIn('kallisto',quant['tools']);self.assertNotIn('salmon',quant['tools'])
  step=self.step('singlecell','singlecell_analyze')
  self.assertNotIn('harmonypy',e.requirements(step)['modules'])
  self.assertIn('harmonypy',e.requirements(step,{}, {'integration':'harmony'})['modules'])
  self.assertIn('scrublet',e.requirements(step,{}, {'doublets':{'enabled':True}})['modules'])
  self.assertIn('r-igraph',e.packages({'r':['igraph']}));self.assertIn('python-igraph>=0.11,<2',e.packages({'modules':['igraph']}))

 def test_legacy_deeptools_keeps_compatible_numeric_dependencies(self):
  node=next(n for n in ast.parse(e.NEXTFLOW_PREPARE).body if isinstance(n,ast.FunctionDef) and n.name=='compatible_dependencies');scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),'<dependency compatibility>','exec'),scope)
  original=['deeptools=3.5.1'];actual=scope['compatible_dependencies'](original)
  self.assertIn('matplotlib<3.8',actual);self.assertIn('scipy<1.11',actual);self.assertEqual(original,['deeptools=3.5.1'])
  self.assertEqual(scope['compatible_dependencies'](['deeptools=3.5.6']),['deeptools=3.5.6'])

 def test_legacy_multiqc_keeps_its_runtime_api_and_other_versions_unchanged(self):
  node=next(n for n in ast.parse(e.NEXTFLOW_PREPARE).body if isinstance(n,ast.FunctionDef) and n.name=='compatible_dependencies');scope={};exec(compile(ast.Module(body=[node],type_ignores=[]),'<dependency compatibility>','exec'),scope)
  actual=scope['compatible_dependencies'](['multiqc=1.13']);self.assertIn('python=3.10',actual);self.assertIn('setuptools<82',actual)
  self.assertEqual(scope['compatible_dependencies'](['multiqc=1.23']),['multiqc=1.23'])

if __name__=='__main__':unittest.main()
