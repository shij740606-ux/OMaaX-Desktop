import importlib.util,os,tempfile,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
import user_runtime as r
class RuntimeTests(unittest.TestCase):
 def test_existing_runtime_never_installs(self):
  with patch.object(r,'activate'),patch.object(r,'manager',return_value=('conda','/private/bin/conda')),patch.object(r.subprocess,'run') as run:r.install();run.assert_not_called()
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
   with patch.object(Path,'home',return_value=Path(folder)),patch.object(r.shutil,'which',return_value=None),patch.object(r.sys,'executable',str(Path(folder)/'python3')),patch.dict(os.environ,{'CONDA_EXE':''}),patch.object(r.os,'access',return_value=True),patch.object(r.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'conda 24','')):
    self.assertEqual(r.manager(),('conda',str(executable)))
 def test_broken_conda_is_skipped_for_working_alternative(self):
  with tempfile.TemporaryDirectory() as folder:
   home=Path(folder);bad=home/'broken/conda';good=home/'miniforge3/bin/conda'
   for file in [bad,good]:file.parent.mkdir(parents=True);file.touch()
   def probe(args,**kwargs):
    self.assertNotIn('PYTHONHOME',kwargs['env']);self.assertNotIn('PYTHONPATH',kwargs['env'])
    return subprocess.CompletedProcess(args,1 if args[0]==str(bad) else 0,'','GLIBC_2.28 not found' if args[0]==str(bad) else '')
   with patch.object(Path,'home',return_value=home),patch.object(r.shutil,'which',side_effect=lambda n:str(bad) if n=='conda' else None),patch.object(r.sys,'executable',str(bad.parent/'python')),patch.dict(os.environ,{'CONDA_EXE':str(bad),'PYTHONHOME':'broken','PYTHONPATH':'broken'}),patch.object(r.os,'access',return_value=True),patch.object(r.subprocess,'run',side_effect=probe):
    self.assertEqual(r.manager(),('conda',str(good)))
 def test_hung_installer_is_not_accepted(self):
  with patch.object(r.shutil,'which',return_value='/broken/conda'),patch.object(Path,'is_file',return_value=True),patch.object(r.os,'access',return_value=True),patch.object(r.subprocess,'run',side_effect=subprocess.TimeoutExpired('conda',10)):
   self.assertIsNone(r.manager())
 @unittest.skipUnless(os.name=='posix','Linux executable compatibility')
 def test_real_broken_executable_falls_back_without_changing_it(self):
  with tempfile.TemporaryDirectory() as folder:
   home=Path(folder);bad=home/'broken/conda';good=home/'miniforge3/bin/conda'
   for file,text in [(bad,"#!/bin/sh\necho 'GLIBC_2.28 not found' >&2\nexit 127\n"),(good,"#!/bin/sh\necho 'conda 24.11.3'\n")]:
    file.parent.mkdir(parents=True);file.write_text(text);file.chmod(0o700)
   original=bad.read_bytes()
   with patch.object(Path,'home',return_value=home),patch.object(r.sys,'executable',str(bad.parent/'python')),patch.dict(os.environ,{'PATH':str(bad.parent),'CONDA_EXE':str(bad)}):
    self.assertEqual(r.manager(),('conda',str(good)))
   self.assertEqual(bad.read_bytes(),original)
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
