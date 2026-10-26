"""Use the shipped CRT for actual PyMuPDF import, independently of system CRT."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
desktop = Path(__file__).resolve().parents[1]
service = Path(os.environ.get('OMAAX_EDUCATION_TEST_SERVICE', desktop / 'resources/education-service'))
resources = Path(os.environ.get('OMAAX_EDUCATION_TEST_DOCUMENTS', desktop / 'resources/documents'))
spec = importlib.util.spec_from_file_location('education_bootstrap', service / 'serve.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class NativeRuntime(unittest.TestCase):
    def test_missing_or_changed_runtime_fails_before_loading(self):
        if os.name != 'nt':
            self.skipTest('Windows runtime')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(RuntimeError, 'Repair or reinstall'):
                bootstrap.configure_windows_runtime(root)
            (root / 'bad.dll').write_bytes(b'changed runtime')
            (root / 'windows-crt.lock.json').write_text(json.dumps({'helperDirectory': '.', 'files': {'bad.dll': {'bytes': 15, 'sha256': '0' * 64}}}))
            with patch.object(os, 'add_dll_directory', side_effect=AssertionError('Must reject before loading')):
                with self.assertRaisesRegex(RuntimeError, 'Repair or reinstall'):
                    bootstrap.configure_windows_runtime(root)

    def test_native_package_loads_bundled_cpp_runtime(self):
        if os.name != 'nt':
            self.skipTest('Windows runtime')
        handle = bootstrap.configure_windows_runtime(resources)
        self.assertIsNotNone(handle)
        try:
            import pymupdf
            document = pymupdf.open()
            document.new_page().insert_text((72, 72), 'Packaged education runtime')
            self.assertGreater(len(document.tobytes()), 100)
            document.close()
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            kernel.GetModuleHandleW.restype = ctypes.c_void_p
            kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
            buffer = ctypes.create_unicode_buffer(32768)
            module = kernel.GetModuleHandleW('MSVCP140.dll')
            self.assertTrue(module)
            kernel.GetModuleFileNameW(module, buffer, len(buffer))
            expected = resources / 'node_modules/@deepseek-ai/libreoffice-kit-win32-x64/bin/msvcp140.dll'
            self.assertEqual(Path(buffer.value).resolve(), expected.resolve())
            print(json.dumps({'nativePdfGenerated': True, 'MSVCP140LoadedFrom': buffer.value, 'bundledRuntime': True}))
        finally:
            handle.close()

    def test_non_windows_and_standalone_service_do_not_require_windows_bundle(self):
        self.assertIsNone(bootstrap.configure_windows_runtime(None))
        with patch.object(os, 'name', 'posix'):
            self.assertIsNone(bootstrap.configure_windows_runtime('unused'))


if __name__ == '__main__':
    unittest.main()
