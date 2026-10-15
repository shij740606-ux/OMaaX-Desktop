import importlib.util,json,re,sys,tempfile,unittest,zipfile
from pathlib import Path
HERE=Path(__file__).parent
sys.path.insert(0,str(HERE))
import workflow_engine as w
import workflow_environments as e
spec=importlib.util.spec_from_file_location('extension_adapter',HERE/'workflow_adapter.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
CATALOG=json.loads((HERE.parent/'workflows/catalog.json').read_text(encoding='utf-8'))
class Extensions(unittest.TestCase):
 def test_all_three_expanded_flows_have_actual_stage_sources_and_reports(self):
  with zipfile.ZipFile(HERE.parent/'workflows/kernel.zip') as z:
   registry=json.loads(z.read('omaax_extensions/stages.json'))
   for fid,minimum in [('singlecell',16),('spatial',17),('metagenome',10)]:
    flow=next(f for f in CATALOG['workflows'] if f['id']==fid)
    self.assertGreaterEqual(len(flow['nodes']),minimum)
    for step in flow['nodes']:
     if step.get('extensionStage'):
      self.assertIn(step['id'],registry);self.assertEqual(registry[step['id']]['outputs'],step['contract']['required_outputs']);self.assertTrue(z.read(step['entry']))
    self.assertIn(fid+'_report',flow['defaultSteps'])
 def test_epigenetic_sources_have_pinned_versions_licenses_and_offline_routes(self):
  with zipfile.ZipFile(HERE.parent/'workflows/kernel.zip') as z:
   for fid in ('atacseq','chipseq','cutandrun','methylseq'):
    flow=next(f for f in CATALOG['workflows'] if f['id']==fid);s=flow['nodes'][0]
    self.assertTrue(z.read(s['nextflowSource']+'/LICENSE'));self.assertTrue(z.read(s['entry']))
    cmd=a.nextflow_command('/bin/nextflow',Path('/kernel'),['run',s['fixedNextflow'],'-params-file','/output/params.json'],Path('/output/runtime.config'))
    self.assertIn('-offline',cmd);self.assertIn(str(Path('/kernel')/s['nextflowSource']),cmd)
    self.assertFalse(any('github.com' in v for v in cmd))
 def test_automatic_artifacts_only_refer_to_earlier_real_steps(self):
  for flow in CATALOG['workflows']:
   rank={sid:i for i,sid in enumerate(flow['order'])}
   for step in flow['nodes']+flow['extraNodes']:
    for spec in step.get('autoInputs',{}).values():
     self.assertTrue(spec['steps'])
     for sid in spec['steps']:self.assertLess(rank[sid],rank[step['id']])
 def test_missing_predecessor_and_unknown_typed_parameters_are_clear_errors(self):
  flow=next(f for f in CATALOG['workflows'] if f['id']=='singlecell');qc=next(s for s in flow['nodes'] if s['id']=='singlecell_qc')
  with self.assertRaisesRegex(ValueError,'缺少输入'):w.resolve_parameters(flow,[qc],{})
  values,configs=w.resolve_parameters(flow,[qc],{'configs':{'singlecell_qc':{'input':'existing.h5ad'}}});self.assertEqual(configs['singlecell_qc']['input'],'existing.h5ad')
  with self.assertRaises(ValueError):w.resolve_parameters(flow,[qc],{'configs':{'singlecell_qc':{'input':'existing.h5ad','min_genes':'abc'}}})
  with self.assertRaises(ValueError):w.resolve_parameters(flow,[qc],{'configs':{'singlecell_qc':{'input':'existing.h5ad','unknown_threshold':1}}})
 def test_inline_conda_preparation_is_valid_python_and_sources_are_supported(self):
  compile(e.NEXTFLOW_PREPARE.replace('UPSTREAM_ID',repr('fixed')),'fixed-preparation','exec')
  with zipfile.ZipFile(HERE.parent/'workflows/kernel.zip') as z:
   for name in z.namelist():
    if name.startswith('upstream_sources/') and name.endswith('.nf'):
     for m in re.finditer(r'''(?m)^\s*conda\s+(["'])([^\n]*?)\1\s*$''',z.read(name).decode()):
      declaration=m.group(2)
      if '${moduleDir}/environment.yml' not in declaration:self.assertNotRegex(declaration,r'[$/]')
 def test_no_all_workflow_install_for_simple_table_analysis(self):
  step=next(s for f in CATALOG['workflows'] if f['id']=='metagenome' for s in f['nodes'] if s['id']=='metagenome_alpha')
  packages=e.packages(e.requirements(step))
  self.assertNotIn('nextflow',packages);self.assertNotIn('scanpy=1.12.4',packages);self.assertNotIn('bioconductor-dss',packages)
  self.assertIn('bioconductor-dss',e.packages(e.requirements(next(s for f in CATALOG['workflows'] if f['id']=='methylseq' for s in f['nodes'] if s['id']=='methylseq_dmr'))))
 def test_process_preparation_keeps_selected_methylation_and_cell_routes(self):
  methyl=next(f for f in CATALOG['workflows'] if f['id']=='methylseq')['nodes'][0]
  route=e.upstream_selection(methyl,{'aligner':'bwamem'})
  self.assertTrue(e.process_selected('modules/nf-core/rastair/call/environment.yml',route))
  self.assertFalse(e.process_selected('modules/nf-core/bismark/align/environment.yml',route))
  route=e.upstream_selection(methyl,{'aligner':'bismark_hisat'})
  self.assertTrue(e.process_selected('modules/nf-core/bismark/align/environment.yml',route));self.assertTrue(e.process_selected('modules/nf-core/hisat2/build/environment.yml',route))
  cell=next(f for f in CATALOG['workflows'] if f['id']=='singlecell')['nodes'][0]
  route=e.upstream_selection(cell,{'aligner':'star'})
  self.assertFalse(e.process_selected('modules/nf-core/simpleaf/index/environment.yml',route))
  self.assertTrue(e.process_selected('modules/nf-core/star/align/environment.yml',route))
  with self.assertRaises(ValueError):e.upstream_selection(cell,{'aligner':'unknown'})
if __name__=='__main__':unittest.main()
