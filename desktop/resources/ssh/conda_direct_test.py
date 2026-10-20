import os,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import conda_runtime as c
class DirectTests(unittest.TestCase):
 def test_no_privileged_or_isolation_probe(self):
  with patch.object(c.os,'getuid',return_value=1000,create=True),patch.object(c.subprocess,'run') as run:
   result=c.protection();self.assertFalse(result['isolation']);self.assertFalse(result['readonlyInputs']);run.assert_not_called()
 def test_virtual_paths_translate_once_and_preserve_unrelated_names(self):
  self.assertEqual(c.translate('/input/a/file /output/result /output_extra',{'/input/a':'/home/u/input','/output':'/home/u/output'}),'/home/u/input/file /home/u/output/result /output_extra')
 def test_script_adaptation_preserves_source_and_separates_output(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);out=base/'task/output';out.mkdir(parents=True);script=base/'script.py';original="open('/output/result','w').write('ok')";script.write_text(original)
   with patch.object(c.shutil,'which',return_value='/python'):
    args,env,cwd=c.command(base/'env',['python3','/input/script'],out,[(script,'/input/script')])
   self.assertEqual(script.read_text(),original);self.assertNotEqual(args[1],str(script));self.assertIn(str(out),Path(args[1]).read_text());self.assertEqual(cwd,str(out));self.assertEqual(env['CONDA_PREFIX'],str(base/'env/conda'))
 def test_concurrent_tasks_have_distinct_cache_and_no_secret_arguments(self):
  with tempfile.TemporaryDirectory() as folder,patch.object(c.shutil,'which',return_value='/python'):
   base=Path(folder);one=base/'one/output';two=base/'two/output';one.mkdir(parents=True);two.mkdir(parents=True)
   _,a,_=c.command(base/'env',['python3'],one);_,b,_=c.command(base/'env',['python3'],two)
   self.assertNotEqual(a['TMPDIR'],b['TMPDIR'])
   with patch.dict(os.environ,{'PRIVATE_TOKEN':'do-not-expose-in-argv'}),patch.object(c,'resolve',return_value=base/'env'):
    self.assertNotIn('PRIVATE_TOKEN=do-not-expose-in-argv',c.args(None,'ref',['python3']))
 def test_failed_install_never_publishes_environment(self):
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder)
   with patch.object(c,'network_run',return_value=1),patch.object(c.user_runtime,'manager',return_value=('conda','/conda')),patch.object(c.user_runtime,'cached_explicit',return_value=None):
    with self.assertRaisesRegex(ValueError,'原有环境未改变'):c.create(types.SimpleNamespace(root=lambda:base),['missing'],['conda-forge'],None)
   self.assertEqual(list(base.rglob('ready.json')),[])
if __name__=='__main__':unittest.main()
