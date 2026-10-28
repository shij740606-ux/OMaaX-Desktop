"""Offline PDF fixtures in temporary directories; no user files or downloaded packages."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.dont_write_bytecode = True
import pymupdf

source = Path(__file__).resolve().parents[1] / 'resources/scripts/figure-pdf-worker.py'
spec = importlib.util.spec_from_file_location('figure_pdf_worker', source)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def fixture(pages=3):
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page(width=240, height=180)
        page.insert_text((20, 30), f'Study {index + 1}: measured 12.34')
        page.draw_rect((20, 50, 100, 100), color=(0.1, 0.4, 0.8))
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 3, 3), 0)
        pixmap.clear_with(128)
        page.insert_image((120, 50, 150, 80), pixmap=pixmap)
    return document


class PdfImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='omaax-figure-pdf-')
        self.root = Path(self.temp.name)
        with fixture() as doc:
            self.raw = doc.tobytes()

    def tearDown(self):
        self.temp.cleanup()

    def inspect(self, raw=None, state=None):
        return worker.run({'action': 'inspect', 'state': str(state or self.root), 'name': 'multi.pdf',
                           'dataUrl': 'data:application/pdf;base64,' + base64.b64encode(raw or self.raw).decode()})

    def test_multipage_preview_vector_and_whole_page_import_preserve_source(self):
        value = self.inspect()
        self.assertEqual(value['totalPages'], 3)
        self.assertEqual([p['page'] for p in value['pages']], [1, 2, 3])
        self.assertTrue(all(p['preview'].startswith('data:image/png;base64,') for p in value['pages']))
        imported = worker.run({'action': 'import', 'state': str(self.root), 'token': value['token'], 'pages': [3, 1], 'mode': 'editable'})
        self.assertEqual([p['page'] for p in imported['items']], [3, 1])
        self.assertIn('<text', imported['items'][0]['svg'])
        self.assertIn('<path', imported['items'][0]['svg'])
        self.assertIn('data:image/png;base64,', imported['items'][0]['svg'])
        page = worker.run({'action': 'import', 'state': str(self.root), 'token': value['token'], 'pages': [2], 'mode': 'page'})['items'][0]
        self.assertTrue(page['dataUrl'].startswith('data:image/png;base64,'))
        self.assertEqual((self.root / (value['token'] + '.pdf')).read_bytes(), self.raw)

    def test_tokens_are_scoped_and_invalid_pages_are_rejected(self):
        token = self.inspect()['token']
        for pages, mode in [([0], 'editable'), ([4], 'page'), ([1, 1], 'page'), ([True], 'page'), ([], 'page'), ([1], 'bad')]:
            with self.assertRaises(ValueError):
                worker.run({'action': 'import', 'state': str(self.root), 'token': token, 'pages': pages, 'mode': mode})
        with self.assertRaises(ValueError):
            worker.run({'action': 'import', 'state': str(self.root / 'another-account'), 'token': token, 'pages': [1], 'mode': 'page'})
        with self.assertRaises(ValueError):
            worker.token_paths(self.root, '../../other')

    def test_encrypted_damaged_and_excessive_page_documents_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'PDF'):
            self.inspect(b'%PDF-invalid')
        with fixture(1) as doc:
            encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='private')
        with self.assertRaisesRegex(ValueError, '加密'):
            self.inspect(encrypted)
        with fixture(101) as doc:
            many = doc.tobytes()
        with self.assertRaisesRegex(ValueError, '100'):
            self.inspect(many)

    def test_expired_cache_and_non_pdf_data_are_rejected(self):
        value = self.inspect()
        metadata = self.root / (value['token'] + '.json')
        saved = json.loads(metadata.read_text())
        saved['created'] = 0
        metadata.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, '过期'):
            worker.run({'action': 'import', 'state': str(self.root), 'token': value['token'], 'pages': [1], 'mode': 'page'})
        with self.assertRaises(ValueError):
            worker.run({'action': 'inspect', 'state': str(self.root), 'dataUrl': 'data:text/plain;base64,dGVzdA=='})

    def test_missing_fonts_warn_and_failed_vector_conversion_falls_back_to_image(self):
        token = self.inspect()['token']
        request = {'action': 'import', 'state': str(self.root), 'token': token, 'pages': [1], 'mode': 'editable', 'availableFonts': ['Different Font']}
        item = worker.run(request)['items'][0]
        self.assertTrue(any('未找到' in warning for warning in item['warnings']))
        with patch.object(pymupdf.Page, 'get_svg_image', side_effect=ValueError('synthetic conversion failure')):
            item = worker.run(request)['items'][0]
        self.assertNotIn('svg', item)
        self.assertTrue(item['dataUrl'].startswith('data:image/png;base64,'))
        self.assertTrue(item['warnings'])

    def test_large_page_png_is_reduced_to_the_frontend_raster_limit(self):
        sizes = iter([11_500_001, 12])
        page = SimpleNamespace(rect=SimpleNamespace(width=400, height=300),
                               get_pixmap=lambda **kwargs: SimpleNamespace(width=400, height=300, tobytes=lambda _: b'x' * next(sizes)))
        warnings = []
        self.assertLess(len(worker.image_url(page, 4000, 200, warnings)), 16_000_000)
        self.assertEqual(len(warnings), 1)


if __name__ == '__main__':
    unittest.main()
