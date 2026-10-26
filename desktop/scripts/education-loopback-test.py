"""Loopback company requests ignore system proxies; remote service behavior is retained."""
import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, os.environ.get('OMAAX_EDUCATION_TEST_SERVICE', str(Path(__file__).resolve().parents[1] / 'resources/education-service')))
import httpx
from app.services import llm


class LoopbackPolicy(unittest.TestCase):
    def check_endpoint(self, endpoint, trust_env):
        original = httpx.AsyncClient
        seen = []

        def client(**kwargs):
            seen.append(kwargs.get('trust_env', True))
            kwargs['transport'] = httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': [{'message': {'content': 'fixture response'}}]}))
            return original(**kwargs)

        with patch.dict(os.environ, {'REVIEW_LLM_BASE_URL': endpoint, 'REVIEW_LLM_API_KEY': 'fixture-only', 'REVIEW_LLM_MODEL': 'omaax'}, clear=True), patch.object(httpx, 'AsyncClient', client):
            self.assertEqual(asyncio.run(llm.complete_text('system', 'question')), 'fixture response')
        self.assertEqual(seen, [trust_env])

    def test_company_loopback_bypasses_system_proxy(self):
        for host in ['127.0.0.1', 'localhost', '[::1]']:
            with self.subTest(host=host):
                self.check_endpoint(f'http://{host}:12345/v1', False)

    def test_remote_standalone_service_retains_proxy_behavior(self):
        self.check_endpoint('https://models.example.invalid/v1', True)


if __name__ == '__main__':
    unittest.main()
