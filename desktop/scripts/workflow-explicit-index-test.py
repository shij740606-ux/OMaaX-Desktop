import ast,tempfile,typing,unittest,zipfile
from pathlib import Path
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]

class ExplicitIndexTests(unittest.TestCase):
 def test_complete_user_index_does_not_require_species_file_names(self):
  with zipfile.ZipFile(ROOT/'resources/workflows/kernel.zip') as z:
   module=ast.parse(z.read('cli/run_rnaseq_align.py').decode())
  function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='resolve_locked_hisat2_index')
  resolver=Mock(side_effect=ValueError('No species naming should be required'))
  scope={'Path':Path,'Optional':typing.Optional,'resolve_rnaseq_project_context':resolver}
  exec(compile(ast.Module(body=[function],type_ignores=[]),'frozen-align-entry','exec'),scope)
  base=ROOT.parent/'deploy/local/frontend-e2e-20261015/index-unit';base.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(dir=base) as folder:
   for suffix in ('ht2','ht2l'):
    prefix=Path(folder)/('custom-reference-'+suffix)
    for i in range(1,9):Path(f'{prefix}.{i}.{suffix}').write_bytes(b'test-index-part')
    self.assertEqual(scope['resolve_locked_hisat2_index'](prefix,Path(folder),Path(folder),'test',None),prefix)
   resolver.assert_not_called()
   with self.assertRaisesRegex(ValueError,'No species'):
    scope['resolve_locked_hisat2_index'](Path(folder)/'incomplete',Path(folder),Path(folder),'test',None)

if __name__=='__main__':unittest.main()
