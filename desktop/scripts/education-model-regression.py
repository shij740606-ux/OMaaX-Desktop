"""Model failure and output-budget regression; all network responses are synthetic."""
import ast
import asyncio
import io
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SERVICE = Path(__file__).resolve().parents[1] / 'resources' / 'education-service'
sys.path.insert(0, str(SERVICE))
sys.dont_write_bytecode = True
import httpx
from starlette.datastructures import UploadFile
from app.services import llm, journal_rank, format_extractor, ppt_outline
from app.routers import presubmit, rebuttal


class ModelRegression(unittest.IsolatedAsyncioTestCase):
    async def test_no_client_output_cap_and_valid_json(self):
        seen = []
        def respond(req):
            seen.append(json.loads(req.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await llm._chat_once(client, {'base_url':'http://127.0.0.1/v1','api_key':'test','model':'omaax','timeout':10}, 's', 'u', temperature=0.2, max_tokens=None, json_mode=True, timeout=None)
        self.assertEqual(json.loads(result), {'ok': True})
        self.assertNotIn('max_tokens', seen[0])
        self.assertEqual(seen[0]['response_format']['type'], 'json_object')

    async def test_truncated_response_is_not_accepted_as_complete(self):
        def respond(req):
            return httpx.Response(200, json={'choices':[{'finish_reason':'length','message':{'content':'{"partial":true}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(llm.LLMError) as error:
                await llm._chat_once(client, {'base_url':'http://127.0.0.1/v1','api_key':'test','model':'omaax','timeout':10}, 's', 'u', temperature=0.2, max_tokens=None, json_mode=True, timeout=None)
        self.assertEqual(error.exception.code, 'OUTPUT_TRUNCATED')

    async def test_service_error_codes_are_safe_and_distinct(self):
        real_client = httpx.AsyncClient
        cases = [(401, 'model_credential_rejected', 'MODEL_AUTH'), (401, 'auth_session_expired', 'AUTH_EXPIRED'),
                 (403, 'model_access_denied', 'MODEL_FORBIDDEN'), (400, 'context_length_exceeded', 'INPUT_TOO_LONG'),
                 (400, 'model_request_invalid', 'MODEL_REQUEST_INVALID'), (429, 'x', 'RATE_LIMIT'), (504, 'x', 'TIMEOUT')]
        for status, bridge_code, expected in cases:
            def respond(req, status=status, bridge_code=bridge_code):
                return httpx.Response(status, json={'error':{'code':bridge_code, 'message':'private backend detail'}})
            def factory(**kwargs):
                return real_client(transport=httpx.MockTransport(respond), **kwargs)
            with self.subTest(code=expected), patch.object(llm.httpx, 'AsyncClient', factory), patch.object(llm, 'MAX_ATTEMPTS', 1), patch.dict(os.environ, {'REVIEW_LLM_API_KEY':'test','REVIEW_LLM_BASE_URL':'http://127.0.0.1/v1'}):
                with self.assertRaises(llm.LLMError) as error:
                    await llm.complete_json('s','u',max_tokens=None)
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn('private backend detail', str(error.exception))

    async def test_network_and_read_timeout_are_distinct(self):
        real_client=httpx.AsyncClient
        for exception, expected in [(httpx.ConnectError('private'), 'NETWORK'), (httpx.ReadTimeout('private'), 'TIMEOUT')]:
            def respond(req, error=exception): raise error
            def factory(**kwargs): return real_client(transport=httpx.MockTransport(respond), **kwargs)
            with self.subTest(code=expected), patch.object(llm.httpx, 'AsyncClient', factory), patch.object(llm, 'MAX_ATTEMPTS', 1), patch.dict(os.environ, {'REVIEW_LLM_API_KEY':'test','REVIEW_LLM_BASE_URL':'http://127.0.0.1/v1'}):
                with self.assertRaises(llm.LLMError) as error:
                    await llm.complete_json('s','u')
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn('private', str(error.exception))

    async def test_presubmit_uses_provider_default_and_returns_typed_error(self):
        upload=UploadFile(file=io.BytesIO(b'fake pdf'), filename='test.pdf')
        mock=AsyncMock(side_effect=llm.LLMError('truncated', 'OUTPUT_TRUNCATED'))
        with patch.object(presubmit.paper_parse, 'parse_pdf_bytes', AsyncMock(return_value='test paper')), patch.object(llm, 'complete_json', mock):
            result=await presubmit.presubmit_review(file=upload, target_journal='')
        self.assertFalse(result.ok)
        self.assertEqual(result.error['code'], 'OUTPUT_TRUNCATED')
        self.assertIsNone(mock.await_args.kwargs['max_tokens'])

    async def test_preprocessed_input_truncation_is_reported(self):
        upload=UploadFile(file=io.BytesIO(b'fake pdf'), filename='test.pdf')
        mock=AsyncMock(return_value={})
        with patch.object(presubmit.paper_parse, 'parse_pdf_bytes', AsyncMock(return_value='x' * 120000)), patch.object(llm, 'complete_json', mock):
            result=await presubmit.presubmit_review(file=upload, target_journal='')
        self.assertTrue(result.ok)
        self.assertTrue(result.meta['truncated'])
        self.assertLess(result.meta['text_chars'], 120000)

    async def test_invalid_json_is_classified(self):
        with patch.object(llm, '_complete', AsyncMock(side_effect=['bad','still bad'])):
            with self.assertRaises(llm.LLMError) as error:
                await llm.complete_json('s','u')
        self.assertEqual(error.exception.code, 'MODEL_INVALID_RESPONSE')

    async def test_other_model_features_use_the_provider_budget(self):
        mock=AsyncMock(return_value={'claims':[], 'figures':[]})
        with patch.object(llm, 'complete_json', mock):
            claims, figures=await ppt_outline._map_block({'section_id':'S1','text':'synthetic'})
        self.assertEqual((claims, figures), ([], []))
        self.assertIsNone(mock.await_args.kwargs['max_tokens'])
        self.assertEqual(mock.await_args.kwargs['timeout'], 360)

        mock=AsyncMock(return_value={})
        with patch.object(llm, 'complete_json', mock), patch.object(journal_rank, 'validate_rankings', return_value=([],0,0)):
            await journal_rank.rerank('synthetic abstract', [], {})
        self.assertIsNone(mock.await_args.kwargs['max_tokens'])
        self.assertEqual(mock.await_args.kwargs['timeout'], 360)

        mock=AsyncMock(return_value={})
        with patch.object(format_extractor, 'complete_json', mock):
            await format_extractor.extract_format_rules('synthetic guidelines', 'test journal')
        self.assertIsNone(mock.await_args.kwargs['max_tokens'])

        mock=AsyncMock(return_value={'ok':True})
        with patch.object(llm, 'complete_json', mock):
            self.assertEqual(await rebuttal._messages_llm([{'role':'user','content':'test'}]), {'ok':True})
        self.assertIsNone(inspect.signature(llm.complete_json).parameters['max_tokens'].default)

    def test_education_model_calls_do_not_set_fixed_output_caps(self):
        for name in ('services/journal_rank.py','services/format_extractor.py','services/ppt_outline.py','routers/presubmit.py'):
            tree=ast.parse((SERVICE/'app'/name).read_text('utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == 'max_tokens':
                            self.assertIsInstance(kw.value, ast.Constant, name)
                            self.assertIsNone(kw.value.value, name)

if __name__ == '__main__':
    unittest.main()
