"""Shell bootstrap tests; Python is only the local test driver, never the repair entry."""
import os, subprocess, tempfile, unittest
from pathlib import Path

@unittest.skipIf(os.name == 'nt', 'requires POSIX shell')
class EnvironmentRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.runtime=self.home/'omaax/.runtime'
        fake=self.home/'mock/bin';fake.mkdir(parents=True);(fake/'python3').write_text('#!/bin/sh\nexit 0\n');(fake/'python3').chmod(0o700)
        self.source=Path(__file__).with_name('environment_repair.sh').read_text()
    def run_shell(self, action, installer='printf repaired; return 0'):
        script='''omaax_runtime_root="$HOME/omaax/.runtime"
omaax_runtime_env="$HOME/mock"
omaax_find_python() { OMAAX_PYTHON=/test/python; return 0; }
omaax_install_runtime() { INSTALLER; }
omaax_repair_action=ACTION
'''.replace('INSTALLER',installer).replace('ACTION',action)+self.source
        return subprocess.run(['/bin/sh','-c',script],env={**os.environ,'HOME':str(self.home)},capture_output=True,text=True)
    def test_failure_log_is_retrievable_and_original_is_preserved(self):
        original=self.home/'input.txt';original.write_text('preserve')
        result=self.run_shell('repair','printf incompatible >&2; return 17')
        self.assertEqual(result.returncode,17)
        status=self.run_shell('status');self.assertIn('incompatible',status.stdout);self.assertIn('installer_exit=17',status.stdout)
        self.assertEqual(original.read_text(),'preserve');self.assertNotIn('ready=true',result.stdout)
    def test_success_is_verified_and_logged(self):
        result=self.run_shell('repair');self.assertEqual(result.returncode,0);self.assertIn('ready=true',result.stdout)
        self.assertIn('installer_exit=0',self.run_shell('status').stdout)
    def test_failed_import_check_never_reports_ready(self):
        (self.home/'mock/bin/python3').write_text('#!/bin/sh\nexit 1\n')
        result=self.run_shell('repair');self.assertNotEqual(result.returncode,0);self.assertNotIn('ready=true',result.stdout)
    def test_unknown_action_does_not_create_runtime(self):
        self.assertEqual(self.run_shell('unknown').returncode,2);self.assertFalse(self.runtime.exists())
    def test_symlink_runtime_is_rejected(self):
        target=self.home/'other';target.mkdir();(self.home/'omaax').symlink_to(target)
        self.assertNotEqual(self.run_shell('repair').returncode,0);self.assertEqual(list(target.iterdir()),[])
if __name__=='__main__':unittest.main()
