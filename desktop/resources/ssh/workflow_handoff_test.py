import importlib.util,json,sys,tempfile,unittest,zipfile
from pathlib import Path
HERE=Path(__file__).parent
sys.path.insert(0,str(HERE))
import workflow_adapter as adapter
import workflow_engine as engine

class Handoff(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.bundle=zipfile.ZipFile(HERE.parent/'workflows/kernel.zip')
  cls.catalog=json.loads((HERE.parent/'workflows/catalog.json').read_text(encoding='utf-8'))
  cls.temp=tempfile.TemporaryDirectory();cls.base=Path(cls.temp.name)
  cls.bundle.extract('omaax_extensions/upstream_links.py',cls.base)
  spec=importlib.util.spec_from_file_location('upstream_links',cls.base/'omaax_extensions/upstream_links.py');cls.links=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.links)
 @classmethod
 def tearDownClass(cls):cls.bundle.close();cls.temp.cleanup()
 def flow(self,name):return next(f for f in self.catalog['workflows'] if f['id']==name)
 def test_upstream_and_downstream_resolve_in_one_ordered_plan(self):
  for fid,source in [('singlecell','singlecell_upstream'),('spatial','spatial_segment'),('metagenome','metagenome_profile')]:
   flow=self.flow(fid);requested=[*flow['defaultSteps'],source]
   steps=engine.select_steps(flow,requested)
   upstream=next(s for s in steps if s['id']==source)
   body={'configs':{source:{k:('docker' if k=='profile' else 'raw_samples.csv' if k=='input' else k+'.fixture') for k in upstream['contract']['required_config']}}}
   _,configs=engine.resolve_parameters(flow,steps,body)
   self.assertLess([s['id'] for s in steps].index(source),[s['id'] for s in steps].index(fid+'_prepare'))
   self.assertEqual(configs[fid+'_prepare']['samples'],'@auto')
   self.assertEqual(steps[-1]['id'],fid+'_report')
 def test_existing_matrix_and_abundance_paths_still_work_without_upstream(self):
  for fid in ('singlecell','spatial','metagenome'):
   flow=self.flow(fid);steps=engine.select_steps(flow,flow['defaultSteps']);_,configs=engine.resolve_parameters(flow,steps,{})
   self.assertEqual(configs[fid+'_prepare']['samples'],'samples.csv')
   if fid=='metagenome':self.assertEqual(configs[fid+'_prepare']['abundance'],'abundance.tsv')
   else:self.assertEqual(configs[fid+'_prepare']['upstream_results'],'')
 def test_adapter_uses_only_successful_current_task_result_and_original_sheet(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);(base/'final').mkdir();(base/'configs').mkdir();(base/'raw').mkdir()
   flow=self.flow('singlecell');step=next(s for s in flow['nodes'] if s['id']=='singlecell_prepare')
   root=base/'singlecell_upstream/results';root.mkdir(parents=True)
   marker=base/'final/singlecell_upstream.manifest.json';marker.write_text(json.dumps({'status':'success','output_dir':str(root)}))
   plan={'steps':['singlecell_upstream','singlecell_prepare'],'parameters':{},'configs':{'singlecell_upstream':{'input':'raw_samples.csv'},step['id']:dict(step['configDefaults'])},'resources':{'cpu':2}}
   adapter.command(step,plan,base/'kernel',base/'raw',base,base/'configs','/original')
   cfg=json.loads((base/'configs/singlecell_prepare.json').read_text())
   self.assertEqual(cfg['upstream_results'],str(root));self.assertEqual(cfg['samples'],str(base/'raw/raw_samples.csv'))
   marker.unlink()
   with self.assertRaisesRegex(ValueError,'缺少前置'):adapter.command(step,plan,base/'kernel',base/'raw',base,base/'configs','/original')
   marker.write_text(json.dumps({'status':'failed','output_dir':str(root)}))
   with self.assertRaisesRegex(ValueError,'not completed'):adapter.command(step,plan,base/'kernel',base/'raw',base,base/'configs','/original')
 def test_sample_identifiers_and_ambiguous_outputs(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);sheet=root/'raw.csv';sheet.write_text('sample,fastq_1,group\nS1,a.fq,control\nS1_2,b.fq,treated\n')
   target=root/'star/mtx_conversions';target.mkdir(parents=True)
   for sid in ('S1','S1_2'):(target/(sid+'_filtered_matrix.h5ad')).write_bytes(b'fixture')
   rows=self.links.cell_samples(root,sheet,False)
   self.assertEqual([r['group'] for r in rows],['control','treated'])
   self.assertTrue(rows[0]['path'].endswith('S1_filtered_matrix.h5ad'))
   other=root/'simpleaf/mtx_conversions';other.mkdir(parents=True);(other/'S1_filtered_matrix.h5ad').write_bytes(b'fixture')
   with self.assertRaisesRegex(ValueError,'2 份候选'):self.links.cell_samples(root,sheet,False)
 def test_spatial_export_and_missing_output_are_distinct(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);sheet=root/'raw.csv';sheet.write_text('sample,data_dir\nsection1,raw\n')
   with self.assertRaisesRegex(ValueError,'需启用 Explorer'):self.links.cell_samples(root,sheet,True)
   path=root/'section1.explorer/adata.h5ad';path.parent.mkdir();path.write_bytes(b'fixture')
   self.assertEqual(self.links.cell_samples(root,sheet,True)[0]['path'],str(path))
 def test_scrnaseq_per_sample_directories_ignore_combined_matrix(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);sheet=root/'samples.csv';sheet.write_text('sample_id,group\nC1,control\nT1,treated\n')
   parent=root/'star/mtx_conversions';parent.mkdir(parents=True)
   (parent/'combined_filtered_matrix.h5ad').write_bytes(b'combined')
   for sid in ['C1','T1']:
    (parent/sid).mkdir();(parent/sid/(sid+'_filtered_matrix.h5ad')).write_bytes(b'filtered');(parent/sid/(sid+'_raw_matrix.h5ad')).write_bytes(b'raw')
   rows=self.links.cell_samples(root,sheet,False)
   self.assertEqual([row['sample_id'] for row in rows],['C1','T1'])
   self.assertEqual(Path(rows[0]['path']).parent.name,'C1')
 def test_epigenetic_replicate_labels_do_not_change_sample_grouping(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);rows=[{'sample_id':'A_R1','group':'control'},{'sample_id':'B_R1','group':'treated'}];paths=[]
   for name in ['A_REP1.mLb.clN.sorted.bam','B_REP1.mLb.clN.sorted.bam']:
    p=root/name;p.write_bytes(b'naming fixture');paths.append(p)
   found=self.links.unique_by_sample(paths,rows,root,self.links.replicate_aliases(rows))
   self.assertEqual(found['A_R1'].name,'A_REP1.mLb.clN.sorted.bam');self.assertEqual(rows[0]['group'],'control')
   with self.assertRaisesRegex(ValueError,'存在歧义'):self.links.replicate_aliases([{'sample_id':'A_R1'},{'sample_id':'A_REP1'}])
 def test_linked_files_cannot_escape_upstream_results(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);outside=root/'outside.h5ad';outside.write_bytes(b'fixture');scope=root/'result';scope.mkdir()
   try:(scope/'escape.h5ad').symlink_to(outside)
   except OSError:self.skipTest('Symlink privileges unavailable on this platform')
   with self.assertRaisesRegex(ValueError,'目录之外'):self.links.files(scope,['*.h5ad'])

 def test_cutandrun_coverage_uses_analysis_tracks_instead_of_igv_copies(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);sheet=root/'samples.csv';sheet.write_text('sample_id,group\nA_R1,control\nB_R1,treated\n')
   canonical=root/'03_peak_calling/03_bed_to_bigwig';canonical.mkdir(parents=True);igv=root/'04_reporting/igv';igv.mkdir(parents=True)
   for sid in ['A_R1','B_R1']:
    (canonical/(sid+'.bigWig')).write_bytes(b'track');(igv/(sid+'.bigWig')).write_bytes(b'track')
   self.assertEqual(self.links.coverage_files(root,sheet),[str(canonical/(sid+'.bigWig')) for sid in ['A_R1','B_R1']])
   (canonical/'A_R1.alternate.bigWig').write_bytes(b'other')
   with self.assertRaisesRegex(ValueError,'2 份候选'):self.links.coverage_files(root,sheet)

if __name__=='__main__':unittest.main()
