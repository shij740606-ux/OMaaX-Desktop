import json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import personal_runner as r


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='resume-'))
        self.job=self.root/'job';self.job.mkdir();(self.job/'output').mkdir()
        self.spec={'id':'job','cpu':1,'memoryMb':128,'timeoutSeconds':10,'pipeline':[{'step':'a','image':'unused'},{'step':'b','image':'unused'}]}
        self.patches=[patch.object(r,'root',return_value=self.root),patch.object(r,'jobdir',return_value=self.job),patch.object(r,'disk_available',return_value=10**12),patch.object(r,'validate_job'),patch.object(r,'launch'),patch.object(r,'command',return_value='[]')]
        for p in self.patches:p.start()
        db=r.database();db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',('job',json.dumps(self.spec),'failed',0,0,''));db.commit()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
    def snapshot(self):
        (self.job/'output/result.txt').write_text('completed a')
        r.checkpoint(self.job,self.spec,['a'])
    @unittest.skipUnless(os.name=='posix','Linux supervisor locking')
    def test_resume_restores_verified_snapshot_and_preserves_failed_output(self):
        self.snapshot();(self.job/'output/result.txt').write_text('failed overwritten output');(self.job/'cancel').touch()
        result=r.resume_job('job')
        self.assertEqual((self.job/'output/result.txt').read_text(),'completed a')
        self.assertEqual((self.job/result['preservedAttempt']/'result.txt').read_text(),'failed overwritten output')
        self.assertFalse((self.job/'cancel').exists());self.assertEqual(r.read_resume(self.job,self.spec),['a'])
        self.assertEqual(r.database().execute('SELECT state FROM jobs').fetchone()[0],'queued')
    @unittest.skipUnless(os.name=='posix','Linux supervisor locking')
    def test_tampered_checkpoint_is_rejected_without_changing_outputs(self):
        self.snapshot();saved=json.loads((self.job/'checkpoint.json').read_text());(self.job/saved['directory']/'output/result.txt').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'校验失败'):r.resume_job('job')
        self.assertEqual((self.job/'output/result.txt').read_text(),'completed a')
    def test_checkpoint_space_failure_keeps_original(self):
        (self.job/'output/result.txt').write_text('important')
        with patch.object(r,'disk_available',return_value=0),self.assertRaises(ValueError):r.checkpoint(self.job,self.spec,['a'])
        self.assertEqual((self.job/'output/result.txt').read_text(),'important')
    @unittest.skipUnless(os.name=='posix','Linux supervisor locking')
    def test_resume_recovers_a_crash_between_archive_and_restore(self):
        self.snapshot();os.rename(self.job/'output',self.job/'attempt-before-crash')
        r.resume_job('job')
        self.assertEqual((self.job/'output/result.txt').read_text(),'completed a')
        self.assertTrue((self.job/'attempt-before-crash/result.txt').is_file())
    def test_resource_admission_limits_cpu_memory_and_count(self):
        with patch.object(Path,'read_text',return_value='MemTotal: 1048576 kB\n'),patch.object(r.os,'cpu_count',return_value=4),patch.object(r,'memory',return_value=1024**3):
            db=r.database();self.assertTrue(r.admission(db,self.spec))
            for i in range(4):db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(str(i),json.dumps(self.spec),'running',0,0,''))
            db.commit();self.assertFalse(r.admission(db,self.spec))
            db.execute('DELETE FROM jobs WHERE id!=?',('job',));db.commit()
            self.assertFalse(r.admission(db,{**self.spec,'cpu':5}));self.assertFalse(r.admission(db,{**self.spec,'memoryMb':2048}))
    @unittest.skipUnless(os.name=='posix','Linux supervisor locking')
    def test_queue_age_does_not_consume_execution_budget_and_resume_skips_a(self):
        self.snapshot();r.resume_job('job');calls=[]
        def execute(job,spec,cfg,directory,started,log):calls.append(spec['argv'][-1]);return 0,False
        # A queue timestamp from 1970 must not timeout before the first execution.
        self.spec.update(argv=['python'],image='unused')
        db=r.database();db.execute('UPDATE jobs SET spec=?',(json.dumps(self.spec),));db.commit()
        (self.job/'resume.json').write_text(json.dumps({'spec':self.spec,'completed':['a']}))
        with patch.object(r,'admission',return_value=True),patch.object(r,'protection'),patch.object(r,'execute_container',side_effect=execute),patch.object(r,'checkpoint'):
            r.run_job('job')
        self.assertEqual(calls,['b']);self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'succeeded')

if __name__=='__main__':unittest.main()
