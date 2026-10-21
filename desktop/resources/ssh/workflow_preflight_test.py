import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
import os
import sys
from contextlib import nullcontext
from unittest.mock import Mock, patch
import workflow_preflight as p
import workflow_engine as engine


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.r = SimpleNamespace(root=lambda: self.root, SERVER_KEY='a'*16,
                                 __file__='/shared/runner.py', dispatch=Mock())
        self.flow = {'name': 'small fixture', 'defaultSteps': ['a']}
        self.body = {'workflow': 'fixture', 'steps': ['a']}

    def start(self, intent=None):
        with (patch.dict(sys.modules, {'fcntl': Mock()}) if os.name == 'nt' else nullcontext()), \
             patch.object(engine, 'flow_for', return_value=self.flow), \
             patch.object(engine, 'select_steps'), patch.object(p.scheduler, 'cluster', return_value='test'), \
             patch.object(p.scheduler, 'submit', return_value={'jobId': '123'}) as submit:
            result = p.start(self.r, {'body': self.body, 'intent': intent})
        self.assertEqual(result['state'], 'preparing')
        self.r.dispatch.assert_not_called()
        self.assertEqual(submit.call_args.args[1][-2], '--workflow-preflight')
        return result['operationId']

    def test_login_node_only_submits(self):
        self.start()

    def test_authorized_plan_submits_without_another_user_confirmation(self):
        intent = {'source': 'user', 'executionRequested': True, 'sessionId': 'test', 'seq': 4}
        plan = {'plan': {'id': 'task-1'}, 'digest': 'fixed', 'confirmed': False,
                'state': 'awaiting_confirmation'}
        self.r.dispatch.return_value = {'id': 'task-1', 'state': 'queued'}
        result = p.complete(self.r, plan, intent)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['state'], 'queued')
        self.assertEqual(self.r.dispatch.call_args.args[0]['body']['intent'], intent)

    def test_plan_only_never_submits(self):
        plan = {'plan': {'id': 'task-1'}, 'confirmed': False, 'state': 'awaiting_confirmation'}
        self.assertEqual(p.complete(self.r, plan, None), plan)
        self.r.dispatch.assert_not_called()

    def test_same_user_intent_has_one_operation(self):
        intent = {'source': 'user', 'executionRequested': True, 'seq': 1, 'sessionId': 'test'}
        self.assertEqual(self.start(intent), self.start(intent))
        self.assertNotEqual(self.start(intent), self.start({**intent, 'seq': 2}))

    @unittest.skipUnless(__import__('os').name == 'posix', 'worker uses Linux file lock')
    def test_worker_requires_allocation_and_preserves_failure(self):
        identity = self.start()
        with patch.object(p.scheduler, 'allocation', return_value=None):
            with self.assertRaises(ValueError):
                p.run(self.r, identity)
        state = json.loads((p.directory(self.r, identity)/'state.json').read_text())
        self.assertEqual(state['state'], 'preparation_failed')
        self.r.dispatch.assert_not_called()

    @unittest.skipUnless(__import__('os').name == 'posix', 'worker uses Linux file lock')
    def test_persisted_plan_reconciles_without_replanning(self):
        intent = {'source': 'user', 'executionRequested': True}
        identity = self.start(intent)
        plan = {'plan': {'id': 'task-1'}, 'digest': 'fixed'}
        p.scheduler.atomic(p.directory(self.r, identity)/'plan.json', plan)
        self.r.dispatch.return_value = {'id': 'task-1', 'reused': True}
        with patch.object(p.scheduler, 'allocation', return_value='123'):
            p.run(self.r, identity)
            p.run(self.r, identity)
        self.r.dispatch.assert_called_once()
        self.assertEqual(self.r.dispatch.call_args.args[0]['action'], 'workflow_confirm')
        self.assertTrue((p.directory(self.r, identity)/'placement.json').is_file())

    def test_rejects_false_intent(self):
        with patch.object(engine, 'flow_for', return_value=self.flow), patch.object(engine, 'select_steps'):
            with self.assertRaises(ValueError):
                p.start(self.r, {'body': self.body, 'intent': {'source': 'agent'}})


if __name__ == '__main__':
    unittest.main()
