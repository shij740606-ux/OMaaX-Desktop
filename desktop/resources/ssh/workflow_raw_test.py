import gzip,hashlib,json,os,re,subprocess,sys,tempfile,unittest,zipfile
from unittest.mock import patch
from pathlib import Path
HERE=Path(__file__).parent;sys.path.insert(0,str(HERE))
import workflow_engine as w
import workflow_environments as e
import workflow_adapter as adapter
CATALOG=json.loads((HERE.parent/'workflows/catalog.json').read_text(encoding='utf-8'))

class RawRoutes(unittest.TestCase):
 def test_mag_builtin_control_references_required_by_source_are_packaged(self):
  with zipfile.ZipFile(HERE.parent/'workflows/kernel.zip') as bundle:
   manifest=json.loads(bundle.read('omaax/reference-assets.json'));assets={item['path']:item for item in manifest['files']};source=bundle.read('upstream_sources/nf-core_mag_5.5.0/workflows/mag.nf').decode()
   required=re.findall(r'\$\{projectDir\}/(assets/data/[^"\s]+\.gz)',source);self.assertTrue(required)
   for name in required:
    path='upstream_sources/nf-core_mag_5.5.0/'+name;raw=bundle.read(path);self.assertEqual(hashlib.sha256(raw).hexdigest(),assets[path]['sha256']);self.assertTrue(gzip.decompress(raw).startswith(b'>'))

 def test_original_legacy_workflows_keep_supported_nextflow_parser(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);metadata=root/'environments.json'
   for upstream,expected in [('nf-core_atacseq_2.1.2','v1'),('nf-core_chipseq_2.1.0','v1'),('nf-core_cutandrun_3.2.2','v1'),('nf-core_methylseq_4.2.0','v1'),('nf-core_mag_5.5.0','v2'),('nf-core_scrnaseq_4.2.0','v2')]:
    metadata.write_text(json.dumps({'upstream':'upstream_sources/'+upstream,'environments':{}}));project=root/upstream;project.mkdir()
    pipeline=root/'kernel/upstream_sources'/upstream;(pipeline/'assets').mkdir(parents=True);(pipeline/'nextflow.config').write_text("plugins { id 'nf-validation' }\n");(pipeline/'assets/multiqc_config.yml').write_text('export_plots: true\ndata_format: yaml\n')
    trim=pipeline/'modules/local/for_patch/trimgalore/main.nf';trim.parent.mkdir(parents=True);trim.write_text('tuple val(meta), path("*.html"), emit: html optional true\n')
    with patch.dict(os.environ,{'OMAAX_NEXTFLOW_ENVS':str(metadata),'NXF_HOME':str(root/'nxf'),'OMP_NUM_THREADS':'8'},clear=True),patch.object(adapter.shutil,'which',side_effect=lambda name:{'nextflow':'/usr/bin/nextflow','gzip':'/usr/bin/gzip'}.get(name)):
     adapter.install_nextflow_shim(root/'kernel',project,{'resources':{'cpu':4}})
     self.assertEqual(os.environ['NXF_SYNTAX_PARSER'],expected)
     self.assertEqual(os.environ['OMP_NUM_THREADS'],'1' if upstream=='nf-core_methylseq_4.2.0' else '8')
     self.assertIn('nf-validation@1.1.3',(pipeline/'nextflow.config').read_text());self.assertIn('export_plots: false',(pipeline/'assets/multiqc_config.yml').read_text());self.assertIn('plots_force_interactive: true',(pipeline/'assets/multiqc_config.yml').read_text())
     self.assertIn('emit: html, optional: true',trim.read_text())
 @unittest.skipUnless(os.name=='posix','The upstream shim runs inside Linux containers')
 def test_minimal_cached_image_gets_zcat_and_resource_limits(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);project=root/'project';project.mkdir();compressed=root/'reads with spaces.gz';compressed.write_bytes(gzip.compress(b'@read\nACGT\n+\nIIII\n'))
   with patch.dict(os.environ,{},clear=True),patch.object(adapter.shutil,'which',side_effect=lambda name:{'nextflow':'/usr/bin/nextflow','gzip':'/usr/bin/gzip'}.get(name)):
    adapter.install_nextflow_shim(root/'kernel',project,{'resources':{'cpu':8,'memoryMb':32768,'timeoutSeconds':7200}})
    output=subprocess.check_output([str(project/'bin/zcat'),str(compressed)])
    self.assertEqual(subprocess.check_output([str(project/'bin/gunzip'),'-c',str(compressed)]),output)
   self.assertEqual(output,b'@read\nACGT\n+\nIIII\n')
   self.assertIn('process.resourceLimits=[cpus:8,memory:32768.MB,time:7200.s]',(project/'omaax-nextflow.config').read_text())
 def flow(self,fid):return next(f for f in CATALOG['workflows'] if f['id']==fid)
 def test_skipped_large_cell_and_spatial_alternatives_are_not_installed(self):
  step=self.flow('singlecell')['nodes'][0];route=e.upstream_selection(step,{'aligner':'star','skip_cellbender':True,'skip_qcatch':True})
  for module in ['simpleaf/index','kallistobustools/count','cellranger/count','cellrangerarc/count','cellbender/removebackground','qcatch']:
   self.assertFalse(e.process_selected('modules/nf-core/'+module+'/environment.yml',route),module)
  self.assertTrue(e.process_selected('modules/local/star_align/main.nf',route))
  step=self.flow('spatial')['nodes'][0];route=e.upstream_selection(step,{'technology':'toy_dataset','use_proseg':True,'use_cellpose':False,'use_stardist':False,'use_baysor':False,'use_comseg':False})
  for module in ['patch_segmentation_cellpose','patch_segmentation_stardist','patch_segmentation_baysor','patch_segmentation_comseg']:
   self.assertFalse(e.process_selected('modules/local/'+module+'/environment.yml',route))
  self.assertTrue(e.process_selected('modules/local/patch_segmentation_proseg/environment.yml',route))
  self.assertFalse(e.process_selected('modules/nf-core/spaceranger/count/main.nf',route))
 def test_declared_optional_metrics_do_not_install_their_software(self):
  step=self.flow('chipseq')['nodes'][0];route=e.upstream_selection(step,{'aligner':'bwa','skip_preseq':True,'skip_plot_profile':True,'skip_spp':True})
  for module in ['preseq/lcextrap','deeptools/computematrix','phantompeakqualtools','star/align','bowtie2/align']:
   self.assertFalse(e.process_selected('modules/nf-core/'+module+'/environment.yml',route),module)
  self.assertTrue(e.process_selected('modules/nf-core/bwa/mem/environment.yml',route))
 def test_mag_binning_does_not_install_unselected_quality_databases(self):
  step=next(s for s in self.flow('metagenome')['nodes'] if s['id']=='metagenome_mag');route=e.upstream_selection(step,{'skip_binqc':True,'skip_metabinner':True,'run_checkm':False,'run_checkm2':False,'run_busco':False,'run_gunc':False})
  for tool in ['checkm','checkm2','busco','gunc']:
   self.assertFalse(e.process_selected('modules/nf-core/'+tool+'/lineagewf/environment.yml',route),tool)
  self.assertTrue(e.process_selected('modules/nf-core/metabat2/metabat2/environment.yml',route))
  for module in ['metabinner_bins','metabinner_kmer','metabinner_metabinner','metabinner_tooshort']:
   self.assertFalse(e.process_selected('modules/local/'+module+'/environment.yml',route),module)
  self.assertFalse(e.process_selected('modules/nf-core/dastool/dastool/environment.yml',route))
  self.assertFalse(e.process_selected('modules/nf-core/bbmap/bbnorm/environment.yml',route))
  self.assertTrue(e.process_selected('modules/nf-core/bbmap/bbnorm/environment.yml',{**route,'bbnorm':True}))
 def test_epigenetic_raw_inputs_and_downstream_are_in_one_plan(self):
  for fid in ['atacseq','chipseq','cutandrun']:
   flow=self.flow(fid);ids=[fid+'_'+name for name in ['upstream','regions','count','sample_qc','coverage','report']]
   ordered=w.select_steps(flow,ids);_,cfg=w.resolve_parameters(flow,ordered,{'configs':{fid+'_upstream':{'input':'samples.csv','fasta':'reference.fa'}}})
   self.assertEqual([s['id'] for s in ordered],ids)
   self.assertEqual(cfg[fid+'_count']['samples'],'@auto')
   self.assertEqual(cfg[fid+'_regions']['peaks'],'@auto')
   self.assertEqual(cfg[fid+'_coverage']['reference_point'],'center')

 def test_disabled_mag_quality_check_does_not_validate_unused_database_url(self):
  step=next(s for s in self.flow('metagenome')['nodes'] if s['id']=='metagenome_mag')
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'configs').mkdir();cfg={**step['configDefaults'],'input':'@input:mag.csv','skip_binqc':True}
   plan={'steps':[step['id']],'parameters':{},'configs':{step['id']:cfg},'resources':{'cpu':4}}
   args=adapter.command(step,plan,root/'kernel',root/'input',root,root/'configs','/original');self.assertEqual(args[:3],['nextflow','run','nf-core/mag'])
   effective=json.loads((root/'configs'/(step['id']+'.json')).read_text());self.assertIsNone(effective['checkm_download_url']);self.assertNotIn('validate_params',effective)
   cfg['skip_binqc']=False;adapter.command(step,plan,root/'kernel',root/'input',root,root/'configs','/original');effective=json.loads((root/'configs'/(step['id']+'.json')).read_text())
   self.assertEqual('checkm_download_url' in effective,'checkm_download_url' in cfg)
 def test_methylation_regions_use_actual_coverage_sample_paths(self):
  flow=self.flow('methylseq');ids=['methylseq_'+name for name in ['upstream','coverage_qc','sample_qc','heatmap','dmr','annotation','report']]
  ordered=w.select_steps(flow,ids);_,cfg=w.resolve_parameters(flow,ordered,{'configs':{'methylseq_upstream':{'input':'samples.csv','fasta':'reference.fa'},'methylseq_dmr':{'control':'C','treated':'T'},'methylseq_annotation':{'gtf':'genes.gtf'}}})
  self.assertEqual(cfg['methylseq_dmr']['samples'],'@auto')
  qc=next(s for s in ordered if s['id']=='methylseq_coverage_qc');self.assertIn('samples.csv',qc['contract']['required_outputs'])
 def test_deprecated_cell_option_uses_supported_option_without_remote_igenomes(self):
  step=self.flow('singlecell')['nodes'][0]
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);(root/'configs').mkdir();cfg={**step['configDefaults'],'input':'@input:samples.csv','fasta':'@input:genome.fa','gtf':'@input:genes.gtf','skip_emptydrops':True}
   plan={'steps':[step['id']],'parameters':{},'configs':{step['id']:cfg},'resources':{'cpu':4}}
   args=adapter.command(step,plan,root/'kernel',root/'input',root,root/'configs','/original')
   self.assertEqual(args[:3],['nextflow','run','nf-core/scrnaseq'])
   effective=json.loads((root/'configs'/(step['id']+'.json')).read_text())
   self.assertTrue(effective['skip_cellbender']);self.assertNotIn('skip_emptydrops',effective);self.assertIsNone(effective['igenomes_base'])

if __name__=='__main__':unittest.main()
