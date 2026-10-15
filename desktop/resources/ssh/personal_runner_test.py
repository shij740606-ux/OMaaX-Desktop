import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

spec = importlib.util.spec_from_file_location('runner', Path(__file__).with_name('personal_runner.py'))
r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
IMAGE = 'docker.io/library/python@sha256:'+'a'*64


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='biodsh-runner-test-'))
        self.control = self.temp/'control'; self.control.mkdir()
        self.data = self.temp/'original'; self.data.mkdir()
        (self.data/'original.txt').write_text('never change me')
        self.patches = [patch.object(r,'ROOT',self.control), patch.object(r,'root',lambda:self.control),
                        patch.object(r.os,'getuid',lambda:1000,create=True), patch.object(r,'launch'),
                        patch.object(r,'protection',return_value={'rootless':True}), patch.object(r,'command',return_value='[]')]
        self.active = [p.start() for p in self.patches]
        r.dispatch({'action':'configure','body':{'sources':{'reads':str(self.data)},'images':[IMAGE]}})
        self.job={'id':'test-job','image':IMAGE,'argv':['python','-c','print(1)'],'sources':['reads'],'cpu':1,'memoryMb':128,'timeoutSeconds':30}

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        # Keep isolated fixtures for inspection. No cleanup ever touches a source directory.

    def test_no_arbitrary_action(self):
        with self.assertRaises(ValueError): r.dispatch({'action':'execute','body':{'command':'rm -rf /'}})

    def test_reject_unapproved_image_gpu_and_sources(self):
        for delta in [{'image':'python:latest'},{'gpu':1},{'sources':['unknown']},{'cpu':0},{'memoryMb':-1},{'argv':'sh -c bad'}]:
            with self.assertRaises(ValueError): r.validate_job(self.job|delta,r.config(r.database()))

    @unittest.skipUnless(os.name == 'posix', 'Linux container mount paths')
    def test_container_hard_boundaries(self):
        args=r.container_args(self.job,r.config(r.database()))
        for flag in ['--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--user=0:0','--pull=never','--pids-limit=512','--cpus=1','--memory=128m']:
            self.assertIn(flag,args)
        self.assertIn(str(self.data)+':/input/reads:ro',args)
        self.assertNotIn('--privileged',args)

    def test_submit_idempotency_and_no_duplicate_launch_data(self):
        r.dispatch({'action':'submit','body':self.job})
        result=r.dispatch({'action':'submit','body':self.job})
        self.assertTrue(result['reused'])
        with self.assertRaises(ValueError): r.dispatch({'action':'submit','body':self.job|{'argv':['other']}})
        self.assertEqual(r.database().execute('SELECT count(*) FROM jobs').fetchone()[0],1)

    def test_cancel_preserves_original_and_outputs(self):
        r.dispatch({'action':'submit','body':self.job})
        r.dispatch({'action':'cancel','body':{'id':self.job['id']}})
        self.assertTrue((r.jobdir(self.job['id'])/'cancel').exists())
        self.assertEqual((self.data/'original.txt').read_text(),'never change me')
        self.assertTrue((r.jobdir(self.job['id'])/'output').is_dir())

    def test_cancel_retries_container_not_created_yet(self):
        with patch.object(r, 'command', side_effect=[ValueError('not found'), self.job['id'], '']) as command:
            self.assertFalse(r.cancel_container(self.job['id']))
            self.assertTrue(r.cancel_container(self.job['id']))
        self.assertEqual(command.call_args.args[0], ['podman', 'stop', '--time', '5', 'biodsh-test-job'])

    @unittest.skipUnless(os.name == 'posix', 'Linux supervisor flock')
    def test_supervisor_retries_early_cancel_until_container_stops(self):
        import io
        r.dispatch({'action':'submit','body':self.job})
        process = Mock(stdout=io.BytesIO(), returncode=137)
        process.poll.side_effect = [None, None, 137]
        def spawn(*args, **kwargs):
            (r.jobdir(self.job['id'])/'cancel').touch()
            return process
        poller = Mock()
        poller.select.return_value = []
        with patch.object(r, 'memory', return_value=1024**3), \
             patch.object(r.os, 'getloadavg', return_value=(0,0,0)), \
             patch.object(r, 'container_args', return_value=['podman','run']), \
             patch.object(r.subprocess, 'Popen', side_effect=spawn), \
             patch.object(r.selectors, 'DefaultSelector', return_value=poller), \
             patch.object(r, 'cancel_container', side_effect=[False, True]) as cancel:
            r.run_job(self.job['id'])
        self.assertEqual(cancel.call_count, 2)
        self.assertEqual(r.database().execute('SELECT state FROM jobs').fetchone()[0], 'cancelled')
        self.assertEqual((self.data/'original.txt').read_text(), 'never change me')

    def test_path_traversal_and_system_roots(self):
        for value in ['../outside',str(self.temp/'outside')]:
            with self.assertRaises(ValueError): r.contained(self.data,value)
        for value in ['', '../bad','x/y']:
            with self.assertRaises(ValueError): r.identifier(value)
        with self.assertRaises(ValueError): r.source_path(str(Path.home()))

    def test_no_export_of_running_results(self):
        r.dispatch({'action':'submit','body':self.job})
        with self.assertRaises(ValueError): r.dispatch({'action':'export','body':{'job':'test-job'}})

    def test_transfer_hash_and_register_only_after_validation(self):
        import hashlib
        transfer='11111111-1111-1111-1111-111111111111'
        directory=Path(r.dispatch({'action':'import_prepare','body':{'id':transfer}})['path'])
        (directory/'data.txt').write_bytes(b'original')
        with self.assertRaises(ValueError): r.dispatch({'action':'import_finish','body':{'id':transfer,'files':[{'name':'data.txt','size':8,'sha256':'bad'}]}})
        self.assertNotIn('import_'+transfer,r.config(r.database())['sources'])
        result=r.dispatch({'action':'import_finish','body':{'id':transfer,'files':[{'name':'data.txt','size':8,'sha256':hashlib.sha256(b'original').hexdigest()}]}})
        cfg=r.config(r.database()); adapted=self.job|{'sources':[result['source']],'mounts':{'reads':result['source']}}
        r.validate_job(adapted,cfg)
        if os.name == 'posix': self.assertIn(str(directory)+':/input/reads:ro',r.container_args(adapted,cfg))
        with self.assertRaises(FileExistsError):r.dispatch({'action':'import_prepare','body':{'id':transfer}})

    def test_import_keeps_sources_registered_during_transfer_validation(self):
        import hashlib
        transfer='22222222-2222-2222-2222-222222222222'
        directory=Path(r.dispatch({'action':'import_prepare','body':{'id':transfer}})['path'])
        (directory/'data.txt').write_bytes(b'original')
        original_config=r.config;calls=0
        def concurrent_config(db):
            nonlocal calls
            snapshot=original_config(db);calls+=1
            if calls==1:
                newer={**snapshot['sources'],'another-plan':str(self.data)}
                other=r.database();other.execute('INSERT OR REPLACE INTO config VALUES(?,?)',('sources',json.dumps(newer)));other.commit();other.close()
            return snapshot
        with patch.object(r,'config',side_effect=concurrent_config):
            r.dispatch({'action':'import_finish','body':{'id':transfer,'files':[{'name':'data.txt','size':8,'sha256':hashlib.sha256(b'original').hexdigest()}]}})
        sources=r.config(r.database())['sources']
        self.assertIn('another-plan',sources);self.assertIn('import_'+transfer,sources);self.assertIn('reads',sources)

    def test_bootstrap_preserves_registered_sources(self):
        with patch('shutil.which',return_value='/bin/podman'):
            r.dispatch({'action':'bootstrap'})
        self.assertEqual(r.config(r.database())['sources']['reads'],str(self.data))
        self.assertTrue((self.control/'shared').is_dir())

    def test_bootstrap_checks_new_image_and_preserves_previous_approval(self):
        new_image='registry.example/python@sha256:'+'b'*64
        calls=[]
        def command(args, **kwargs):
            calls.append(args)
            return json.dumps([{'RepoDigests':[new_image]}]) if args[:3]==['podman','image','inspect'] else ''
        with patch('shutil.which',return_value='/bin/podman'), patch.object(r,'command',side_effect=command):
            r.dispatch({'action':'bootstrap','body':{'image':'registry.example/python:3.12'}})
        self.assertEqual(r.config(r.database())['images'],[IMAGE,new_image])
        self.assertIn(new_image,next(args for args in calls if args[:2]==['podman','run']))

    def test_transfer_rejects_insufficient_space_before_creating_destination(self):
        with patch.object(r,'disk_available',return_value=10):
            with self.assertRaises(ValueError):
                r.dispatch({'action':'import_prepare','body':{'id':'too-large','bytes':11}})
        self.assertFalse((self.control/'imports'/'too-large').exists())
    def test_result_export_selects_files_without_exporting_source_or_following_links(self):
        r.dispatch({'action':'submit','body':self.job})
        db=r.database();db.execute('UPDATE jobs SET state=?',('succeeded',));db.commit()
        out=r.jobdir(self.job['id'])/'output';(out/'result.txt').write_text('result');(out/'private-copy.txt').write_text('not selected')
        files=r.dispatch({'action':'export','body':{'job':self.job['id'],'paths':['result.txt']}})
        self.assertEqual([f['name'] for f in files],['result.txt'])
        with self.assertRaises(ValueError):r.dispatch({'action':'export','body':{'job':self.job['id'],'paths':['../other']}})


if __name__ == '__main__': unittest.main()
