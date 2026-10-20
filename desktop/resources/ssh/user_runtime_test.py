import importlib.util,os,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
import user_runtime as r
class RuntimeTests(unittest.TestCase):
 def test_existing_runtime_never_installs(self):
  with patch.object(r,'activate'),patch.object(r.shutil,'which',return_value='/private/bin/runtime'),patch.object(Path,'is_file',return_value=True),patch.object(r.subprocess,'run') as run:r.install();run.assert_not_called()
 def test_install_is_fixed_unprivileged_script(self):
  with patch.object(r,'activate'),patch.object(r,'manager',side_effect=[None,('micromamba','/private/bin/micromamba')]),patch.object(r.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:r.install()
  args=run.call_args.args[0];self.assertEqual(args[:2],['/bin/sh','-c']);self.assertIn('omaax_install_runtime analysis',args[2]);self.assertNotIn('sudo ',args[2]);self.assertNotIn('apt-get',args[2]);self.assertIn('sha256sum -c',args[2]);self.assertIn('flock -w',args[2])
 def test_download_failure_surfaces_without_fallback(self):
  with patch.object(r,'activate'),patch.object(r,'manager',return_value=None),patch.object(r.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','download failed')):
   with self.assertRaisesRegex(ValueError,'download failed'):r.install()
 def test_site_conda_does_not_bootstrap_micromamba(self):
  with patch.object(r,'activate'),patch.object(r,'manager',return_value=('conda','/shared/apps/conda')),patch.object(r.subprocess,'run') as run:
   r.install();run.assert_not_called()
 def test_runtime_adjacent_conda_discovered(self):
  with tempfile.TemporaryDirectory() as folder:
   executable=Path(folder)/'conda';executable.touch()
   with patch.object(r.shutil,'which',return_value=None),patch.object(r.sys,'executable',str(Path(folder)/'python3')),patch.dict(os.environ,{'CONDA_EXE':''}),patch.object(r.os,'access',return_value=True):
    self.assertEqual(r.manager(),('conda',str(executable)))
 @unittest.skipUnless(os.name=='posix','Linux user runtime')
 def test_conda_python_found_outside_path(self):
  with tempfile.TemporaryDirectory() as folder:
   home=Path(folder);b=home/'miniconda3/bin';b.mkdir(parents=True);(b/'python3').symlink_to(__import__('sys').executable)
   script=Path(r.__file__).with_suffix('.sh').read_text()+'\nomaax_find_python; printf "%s" "$OMAAX_PYTHON"\n'
   p=subprocess.run(['/bin/sh','-c',script],env={**os.environ,'HOME':folder,'PATH':'/nonexistent'},capture_output=True,text=True);self.assertEqual(p.returncode,0);self.assertEqual(p.stdout,str(b/'python3'))
 @unittest.skipUnless(os.name=='posix','Linux user runtime')
 def test_private_config_and_symlink_rejection(self):
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=False):
   env=Path(folder)/'env';(env/'bin').mkdir(parents=True);(env/'bin/podman').touch()
   with patch.object(r,'prefix',return_value=env):
    r.activate();self.assertTrue(os.environ['PATH'].startswith(str(env/'bin')));self.assertNotIn('CONTAINERS_CONF',os.environ)
   link=Path(folder)/'link';link.symlink_to(env,target_is_directory=True)
   with self.assertRaisesRegex(ValueError,'符号链接'):r.safe_directory(link/'child')
if __name__=='__main__':unittest.main()
