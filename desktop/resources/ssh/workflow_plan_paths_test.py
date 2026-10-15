import json,os,unittest
from pathlib import Path
from unittest.mock import patch
import workflow_engine_test as fixtures
w,r,a,IMAGE=fixtures.w,fixtures.r,fixtures.a,fixtures.IMAGE

class PlanPathTests(unittest.TestCase):
 setUp=fixtures.ContractTests.setUp
 tearDown=fixtures.ContractTests.tearDown
 configure=fixtures.ContractTests.configure
 request=fixtures.ContractTests.request
 def link(self,target,name,directory=False):
  path=self.data/name
  try:path.symlink_to(target,target_is_directory=directory)
  except OSError as error:
   if os.name=='nt':self.skipTest('Windows requires symlink privilege; exercised on Linux')
   raise error
  return path
 def test_all_nine_catalog_flows_save_and_list_unconfirmed_plans(self):
  for flow in w.CATALOG['workflows']:
   self.configure(flow['id'])
   sid={'gene_family':'pep','rnaseq':'rnaseq_de'}.get(flow['id'],flow['id']+'_report')
   step=next(s for s in flow['nodes']+flow['extraNodes'] if s['id']==sid)
   body={'workflow':flow['id'],'source':'data','input':'.','steps':[step['id']],'cpu':1,'memoryMb':512,'timeoutSeconds':300}
   if flow['id']=='rnaseq':body['parameters']={'rnaseq_de':{'config':'samples.yaml'}}
   with self.subTest(workflow=flow['id']):
    self.assertTrue(w.dispatch(r,'workflow_validate',body)['valid'])
    record=w.dispatch(r,'workflow_plan',body)
    saved=w.dispatch(r,'workflow_plans',{})
    self.assertTrue(any(p['plan']['id']==record['plan']['id'] and p['state']=='awaiting_confirmation' and not p['confirmed'] for p in saved))
  self.assertEqual(r.database().execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
 def test_absolute_and_relative_chinese_space_directories(self):
  nested=self.data/'样本 A';nested.mkdir();(nested/'测序 1.txt').write_text('input')
  for value in ['样本 A',str(nested)]:
   body=self.request('gene_family');body['input']=value
   record=w.dispatch(r,'workflow_plan',body)
   self.assertIn('测序 1.txt',record['plan']['inputs']);w.load_plan(r,r.database(),{'id':record['plan']['id'],'digest':record['digest']})
 def test_file_links_and_directory_links_keep_logical_names_and_group_readonly_mounts(self):
  raw=self.base/'raw';raw.mkdir();(raw/'actual.fastq').write_text('@read\nACGT\n+\nIIII\n');(raw/'other.txt').write_text('other')
  self.link(raw/'actual.fastq','样本 R1.fastq');self.link(raw,'reads',True)
  body=self.request('gene_family');record=w.dispatch(r,'workflow_plan',body);p=record['plan']
  self.assertIn('样本 R1.fastq',p['inputs']);self.assertIn('reads/actual.fastq',p['inputs'])
  mounts=[f for f in p['externalFiles'] if f.get('inputDirectory')];self.assertEqual(len(mounts),1)
  w.dispatch(r,'workflow_confirm',{'id':p['id'],'digest':record['digest']})
  spec=w.job_spec(p);args=r.container_args(spec,r.config(r.database()));self.assertIn(str(raw)+':'+mounts[0]['target']+':ro',args)
  self.assertEqual((raw/'actual.fastq').read_text(),'@read\nACGT\n+\nIIII\n')
 def test_relative_linked_input_folder_is_canonicalized(self):
  raw=self.base/'raw';raw.mkdir();(raw/'read.txt').write_text('data');self.link(raw,'chosen',True)
  body=self.request('gene_family');body['input']='chosen'
  record=w.dispatch(r,'workflow_plan',body);self.assertEqual(record['plan']['sourceBase'],str(raw));self.assertEqual(record['plan']['input'],'.')
 def test_linked_reference_directory_and_index_preserve_the_requested_prefix(self):
  raw=self.base/'actual';raw.mkdir();ref=self.base/'refs';ref.mkdir()
  for i in (1,2):(raw/f'actual.{i}.ht2').write_text('index')
  self.link(raw,'raw-link',True) # Verify privileges before making reference links.
  for i in (1,2):(ref/f'genome.{i}.ht2').symlink_to(raw/f'actual.{i}.ht2')
  mounts,mapping,_=w.external_inputs(r,{'hisat2_index':str(ref/'genome')},{},self.data,IMAGE)
  self.assertEqual({m['target'] for m in mounts},{mapping[str(ref/'genome')]+f'.{i}.ht2' for i in (1,2)})
  mounts,mapping,_=w.external_inputs(r,{'reference':str(ref)},{},self.data,IMAGE)
  self.assertEqual({m['target'] for m in mounts},{mapping[str(ref)]+f'/genome.{i}.ht2' for i in (1,2)})
  inside=self.data/'local.1.ht2';inside.write_text('local index')
  mounts,mapping,_=w.external_inputs(r,{'hisat2_index':str(self.data/'local')},{},self.data,IMAGE)
  self.assertEqual(mapping[str(self.data/'local')],'__main__:local');self.assertEqual(mounts,[])
 def test_reference_link_retargeting_prevents_confirmation(self):
  first=self.base/'first.fa';first.write_text('>one\nACGT\n');second=self.base/'second.fa';second.write_text('>two\nTGCA\n')
  self.link(first,'privilege-test')
  alias=self.base/'alias.fa';alias.symlink_to(first)
  body=self.request('gene_family');body['parameters']={'pep':{'fasta':str(alias)}}
  record=w.dispatch(r,'workflow_plan',body);alias.unlink();alias.symlink_to(second)
  with self.assertRaisesRegex(ValueError,'参考文件链接目标'):w.load_plan(r,r.database(),{'id':record['plan']['id'],'digest':record['digest']})
 def test_link_target_change_and_content_change_block_confirmation(self):
  first=self.base/'first.txt';first.write_text('first');second=self.base/'second.txt';second.write_text('second')
  link=self.link(first,'read.txt');body=self.request('gene_family');record=w.dispatch(r,'workflow_plan',body)
  link.unlink();link.symlink_to(second)
  with self.assertRaisesRegex(ValueError,'链接目标发生变化'):w.dispatch(r,'workflow_confirm',{'id':record['plan']['id'],'digest':record['digest']})
  record=w.dispatch(r,'workflow_plan',body);second.write_text('changed')
  with self.assertRaisesRegex(ValueError,'输入文件发生变化'):w.dispatch(r,'workflow_confirm',{'id':record['plan']['id'],'digest':record['digest']})
 def test_broken_loop_and_private_links_have_explicit_errors(self):
  link=self.link(self.base/'absent','broken')
  with self.assertRaisesRegex(ValueError,'链接可能失效或循环'):w.input_files(r,self.data)
  link.unlink();link=self.link(self.data,'loop',True)
  with self.assertRaisesRegex(ValueError,'循环链接'):w.input_files(r,self.data)
  link.unlink();private=self.base/'.env';private.write_text('private');self.link(private,'private')
  with self.assertRaisesRegex(ValueError,'凭据'):w.input_files(r,self.data)
 @unittest.skipIf(os.name=='nt','POSIX descriptor copy is tested on Linux')
 def test_linked_and_hardlinked_files_are_copied_as_independent_regular_files(self):
  raw=self.base/'raw.txt';raw.write_text('original');self.link(raw,'input-link.txt');os.link(raw,self.data/'hard.txt')
  files=w.input_files(r,self.data);dest=self.base/'copies';dest.mkdir()
  for name,expected in files.items():
   original=Path(expected.get('sourcePath',str(self.data/name)))
   a.copy_input(original.parent,original.name,dest/name,expected)
   self.assertFalse((dest/name).is_symlink());self.assertNotEqual((dest/name).stat().st_ino,original.stat().st_ino)
  (dest/'input-link.txt').write_text('updated');self.assertEqual(raw.read_text(),'original')
 def test_empty_and_special_inputs_and_invalid_resources_do_not_create_plan_records(self):
  empty=self.base/'empty';empty.mkdir();body=self.request('gene_family');body['input']=str(empty)
  with self.assertRaisesRegex(ValueError,'没有文件'):w.dispatch(r,'workflow_plan',body)
  for key,value in [('cpu',0),('memoryMb',127),('timeoutSeconds',0)]:
   body=self.request('gene_family');body[key]=value
   with self.assertRaises(ValueError):w.dispatch(r,'workflow_plan',body)
  self.assertEqual(r.database().execute('SELECT COUNT(*) FROM workflow_plans').fetchone()[0],0)

if __name__=='__main__':unittest.main()
