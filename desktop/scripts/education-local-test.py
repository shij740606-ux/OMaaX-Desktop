"""Local business regression with synthetic documents; external network is forbidden."""
import asyncio
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVICE = Path(__file__).resolve().parents[1] / 'resources/education-service'
sys.dont_write_bytecode = True
sys.path.insert(0, str(SERVICE))
STATE = tempfile.TemporaryDirectory(prefix='omaax-education-test-')
os.environ['REVIEW_DB_PATH'] = str(Path(STATE.name) / 'review.db')
for field in ('PPT_JOBS_DIR',):
    os.environ['REVIEW_' + field] = str(Path(STATE.name) / field)
from fastapi.testclient import TestClient
from app.main import app
from app.services import llm, paper_parse, ppt_store
import pymupdf
from docx import Document


async def mock_json(*args, **kwargs):
    return {'title': 'Clarify methods', 'severity': 'minor', 'sub_items': [
        {'title': 'Clarify methods', 'body': 'Please explain the methods.', 'confidence': 1}]}


class LocalEducation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = patch('httpx.AsyncClient.send', side_effect=AssertionError('Unexpected network access'))
        cls.network.start()
        cls.model = patch.object(llm, 'complete_json', mock_json)
        cls.model.start()
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.model.stop(); cls.network.stop()

    def test_local_seed_and_directory(self):
        self.assertEqual(self.client.get('/health').json()['status'], 'ok')
        journals = self.client.get('/api/review/journals').json()
        self.assertTrue(journals['ok'], journals)
        self.assertEqual(len(journals['items']), 5)
        for lang, count in [('en', 779), ('zh', 277)]:
            result = self.client.get('/api/review/journals/directory', params={'lang': lang}).json()
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['meta']['total'], count)
        self.assertEqual(len(self.client.get('/api/review/ppt/styles').json()['items']), 14)
        self.assertTrue(self.client.get('/api/review/conferences').json()['ok'])

    def test_pdf_docx_tex_local(self):
        doc = pymupdf.open(); page = doc.new_page(); page.insert_text((72, 72), 'A research manuscript with reproducible methods.')
        pdf = doc.tobytes(); doc.close()
        self.assertIn('reproducible', asyncio.run(paper_parse.parse_pdf_bytes(pdf)))
        word = Document(); word.add_paragraph('Research methods'); buf = io.BytesIO(); word.save(buf)
        for name, content in [('paper.pdf', pdf), ('paper.docx', buf.getvalue()),
                              ('paper.tex', br'\documentclass{article}\begin{document}Methods\end{document}')]:
            result = self.client.post('/api/review/format-check', files={'file': (name, content)}, data={'target_journal': 'rice'}).json()
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['checks'])
        with self.assertRaises(paper_parse.ParseError):
            asyncio.run(paper_parse.parse_pdf_bytes(b'not a pdf'))

    def test_rebuttal_persistence_and_export(self):
        result = self.client.post('/api/review/rebuttal/jobs', json={'text': 'Reviewer 1:\n1. Please explain the methods.'}).json()
        self.assertTrue(result['ok'], result)
        job = result['job']; jid = job['id']; cid = job['reviewers'][0]['comments'][0]['id']
        self.assertEqual(self.client.get('/api/review/rebuttal/jobs/' + jid).json()['job']['id'], jid)
        self.assertTrue(self.client.patch(f'/api/review/rebuttal/jobs/{jid}/comments/{cid}/confirm', json={'confirmed': True}).json()['ok'])
        self.assertTrue(self.client.post(f'/api/review/rebuttal/jobs/{jid}/letter').json()['ok'])
        self.assertEqual(self.client.get(f'/api/review/rebuttal/jobs/{jid}/letter.md').status_code, 200)

    def test_ppt_build_and_local_files(self):
        jid = 'ppt_local_fixture'
        ppt_store.insert_job({'id': jid, 'scenario': 'group_meeting', 'minutes': 10, 'style_id': 'academic-minimal',
            'pages': [{'type': 'title', 'title': '本地科研测试', 'bullets': []},
                      {'type': 'method', 'title': 'Research methods', 'bullets': ['Measured sample', 'Retained evidence'], 'evidence': []}]})
        result = self.client.post(f'/api/review/ppt/jobs/{jid}/build').json()
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.client.get(f'/api/review/ppt/jobs/{jid}/pptx').status_code, 200)
        self.assertEqual(self.client.get(f'/api/review/ppt/jobs/{jid}/preview/1').status_code, 200)


    def test_removed_reformat_routes_are_unavailable(self):
        self.assertEqual(self.client.post('/api/review/reformat').status_code, 404)
        self.assertEqual(self.client.post('/api/review/compile').status_code, 404)

if __name__ == '__main__':
    unittest.main()
