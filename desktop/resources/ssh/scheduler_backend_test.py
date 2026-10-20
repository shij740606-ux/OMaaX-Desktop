import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import scheduler_backend as s


class SchedulerTests(unittest.TestCase):
    def test_accounting_lag_does_not_mean_failure(self):
        with patch.object(s,'command',return_value=''):
            self.assertEqual(s.status({'jobId':'123'})['state'],'UNKNOWN')

    def test_accounting_ignores_other_jobs_and_steps(self):
        with patch.object(s,'command',side_effect=['','123.batch|COMPLETED|0:0\n124|FAILED|1:0\n123|TIMEOUT|0:15']):
            self.assertEqual(s.status({'jobId':'123'})['state'],'TIMEOUT')
    def test_expired_live_job_is_reconciled_from_accounting(self):
        with patch.object(s,'command',side_effect=[ValueError('Invalid job id specified'),'123|NODE_FAIL|0:0']):
            self.assertEqual(s.status({'jobId':'123'})['state'],'NODE_FAIL')
        with patch.object(s,'command',side_effect=ValueError('controller unavailable')):
            with self.assertRaises(ValueError):s.status({'jobId':'123'})

    def test_reject_unverified_allocation(self):
        with patch.object(s.os,'getuid',return_value=1000,create=True),patch.dict(os.environ,{'SLURM_JOB_ID':'123'}),patch.object(s,'command',return_value='UserId=someone(999999) JobState=RUNNING NodeList=node17'):
            with self.assertRaises(ValueError):s.allocation()

    @unittest.skipUnless(os.name=='posix','fcntl is required')
    def test_submission_once_and_uncertain_never_replayed(self):
        resources={'cpu':1,'memoryMb':512,'timeoutSeconds':30}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            with patch.object(s,'command',return_value='123;cluster') as run:
                first=s.submit(path,['/python','--run','test'],resources,'a'*16,'cluster')
                second=s.submit(path,['/python','--run','test'],resources,'a'*16,'cluster')
                self.assertEqual(first,second);self.assertEqual(run.call_count,1)
                self.assertIn('--time=1',run.call_args.args[0])
            with self.assertRaises(ValueError):
                s.submit(path,['/python','--run','different'],resources,'a'*16,'cluster')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            with patch.object(s,'command',side_effect=TimeoutError('acknowledgement lost')) as run:
                with self.assertRaises(TimeoutError):s.submit(path,['/python'],resources,'a'*16,'cluster')
                with patch.object(s,'recover',return_value={'state':'SUBMISSION_UNKNOWN'}):
                    with self.assertRaisesRegex(ValueError,'状态待核实'):s.submit(path,['/python'],resources,'a'*16,'cluster')
                self.assertEqual(run.call_count,1)
            self.assertEqual(json.loads((path/'scheduler.json').read_text())['state'],'SUBMISSION_UNKNOWN')

    def test_lost_acknowledgement_recovers_without_resubmit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            (path/'scheduler.json').write_text(json.dumps({'digest':'a'*64,'created':1700000000,'state':'SUBMISSION_UNKNOWN'}))
            with patch.object(s.os,'getuid',return_value=1000,create=True),patch.object(s,'command',side_effect=['123|omaax-'+('a'*16),'123|omaax-'+('a'*16)+'|']) as run:
                record=s.recover(path)
                self.assertEqual(record['jobId'],'123')
                self.assertTrue(all(call.args[0][0]!='sbatch' for call in run.call_args_list))

if __name__=='__main__':
    unittest.main()
