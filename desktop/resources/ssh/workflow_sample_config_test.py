import json,unittest
from pathlib import Path
import workflow_engine as w

class SampleConfigurationTests(unittest.TestCase):
 def check(self,document,step='rnaseq_qc'):
  folder=Path.cwd();path=str((folder/'samples.yaml').resolve())
  w.validate_sample_documents({'id':'rnaseq'},[{'id':step,'name':'测试步骤'}],{step:{'config':path}},{path:document},folder)
 def test_supported_source_shapes_and_user_experiment_design_are_preserved(self):
  for sample in ({'r1':'one.fq','r2':'two.fq'},['one.fq','two.fq'],{'read1':'one.fq','read2':'two.fq'}):
   self.check({'samples':{'A':sample},'groups':{'A':'user-group'}})
  self.check({'groups':{'A':'control','B':'treated'},'compare':[['control','treated']]},'rnaseq_de')
 def test_list_samples_and_wrong_pair_fields_fail_before_submission(self):
  for document in ({'samples':[{'sample':'A','fastq_1':'one.fq','fastq_2':'two.fq'}]}, {'samples':{'A':{'fastq_1':'one.fq','fastq_2':'two.fq'}}}, {'groups':{'A':'C'}}, {'samples':{'A':['one.fq']}}):
   with self.assertRaises(ValueError):self.check(document)
  with self.assertRaisesRegex(ValueError,'groups'):self.check({'samples':{'A':['one.fq','two.fq']},'groups':['C']})
 def test_explicit_group_mapping_and_comparison_translate_without_guessing_or_overwriting(self):
  value={'samples':{'A':{'r1':'one','r2':'two'},'B':{'group':'treated','group_role':'compare'}},'groups':{'A':'control','B':'treated'},'compare':[['control','treated']]}
  self.check(value,'rnaseq_de');self.assertEqual(value['samples']['A']['condition'],'control');self.assertEqual(value['samples']['A']['group_role'],'control')
  self.assertEqual(value['samples']['B'],{'group':'treated','group_role':'compare'})
  with self.assertRaisesRegex(ValueError,'group_role'):self.check({'samples':{'A':{'group':'control'}}},'rnaseq_de')
 def test_catalog_documents_actual_configuration_shape(self):
  catalog=json.loads((Path(__file__).parent.parent/'workflows/catalog.json').read_text(encoding='utf-8'))
  flow=next(f for f in catalog['workflows'] if f['id']=='rnaseq')
  self.check(flow['inputTemplates']['sample_configuration']['content'])

class EpigeneticSampleConfigurationTests(unittest.TestCase):
 def test_upstream_sample_labels_are_not_silently_used_as_user_declared_groups(self):
  folder=Path.cwd();raw=str((folder/'upstream.csv').resolve());groups=str((folder/'groups.csv').resolve())
  docs={raw:{'__omaax_table__':True,'rows':[{'sample':'A','replicate':'1'},{'sample':'B','replicate':'1'}]},groups:{'__omaax_table__':True,'rows':[{'sample_id':'A_R1','group':'control'},{'sample_id':'B_R1','group':'treated'}]}}
  for fid in ('atacseq','chipseq','cutandrun','methylseq'):
   stage=fid+('_dmr' if fid=='methylseq' else '_differential');producer=fid+('_coverage_qc' if fid=='methylseq' else '_count')
   configs={fid+'_upstream':{'input':raw},stage:{'samples':'@auto','control':'control','treated':'treated'}}
   with self.assertRaisesRegex(ValueError,'分组表'):w.validate_sample_documents({'id':fid},[{'id':stage}],{},docs,folder,configs)
   configs[producer]={'samples':groups}
   w.validate_sample_documents({'id':fid},[{'id':stage}],{},docs,folder,configs)
   self.assertEqual(docs[raw]['rows'][0],{'sample':'A','replicate':'1'})
 def test_matching_native_groups_and_explicit_differential_table_are_allowed(self):
  folder=Path.cwd();raw=str((folder/'samples.csv').resolve());docs={raw:{'__omaax_table__':True,'rows':[{'sample':'A'},{'sample':'B'}]}}
  cfg={'atacseq_differential':{'samples':raw,'control':'A','treated':'B'}}
  w.validate_sample_documents({'id':'atacseq'},[{'id':'atacseq_differential'}],{},docs,folder,cfg)

if __name__=='__main__':unittest.main()
