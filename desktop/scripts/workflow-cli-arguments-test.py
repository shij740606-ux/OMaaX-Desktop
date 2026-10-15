"""Check frozen CLI switch metadata against argparse and the actual adapter."""
import argparse,ast,importlib.util,json,unittest,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
source=ast.parse((ROOT/'scripts/build-workflow-release.py').read_text(encoding='utf-8'))
extractor=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='arguments')
scope={'ast':ast,'managed':set(),'names':{}}
exec(compile(ast.Module(body=[extractor],type_ignores=[]),'argument-extractor','exec'),scope)
spec=importlib.util.spec_from_file_location('adapter',ROOT/'resources/ssh/workflow_adapter.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)

class Switches(unittest.TestCase):
 def test_rna_chain_reads_preceding_stage_outputs_and_standalone_input_is_retained(self):
  catalog=json.loads((ROOT/'resources/workflows/catalog.json').read_text(encoding='utf-8'))
  flow=next(f for f in catalog['workflows'] if f['id']=='rnaseq')
  for sid,previous in [('rnaseq_align','rnaseq_qc'),('rnaseq_quant','rnaseq_align')]:
   step=next(s for s in flow['nodes'] if s['id']==sid)
   plan={'parameters':{sid:{}},'resources':{'cpu':1},'prefix':'test','id':'test','steps':[previous,sid]}
   cmd=adapter.command(step,plan,Path('/kernel'),Path('/raw'),Path('/job'),Path('/configs'),'/raw')
   flag='--input'
   self.assertEqual(cmd[cmd.index(flag)+1],str(Path('/job/final')))
   plan['steps']=[sid];cmd=adapter.command(step,plan,Path('/kernel'),Path('/raw'),Path('/job'),Path('/configs'),'/raw')
   self.assertEqual(cmd[cmd.index(flag)+1],str(Path('/raw')))
 def test_all_frozen_optional_boolean_actions_and_both_values(self):
  catalog=json.loads((ROOT/'resources/workflows/catalog.json').read_text(encoding='utf-8'))
  checked=0
  with zipfile.ZipFile(ROOT/'resources/workflows/kernel.zip') as bundle:
   base=Path('/test')
   for flow in catalog['workflows']:
    for step in flow['nodes']+flow['extraNodes']:
     if step['kind']!='cli' or not step.get('entryPresent'):continue
     text=bundle.read(step['entry']).decode('utf-8')
     for row in scope['arguments'](text):
      if row.get('action')!='boolean_optional':continue
      actual=next(a for a in step['arguments'] if a['key']==row['key'])
      self.assertEqual(actual['action'],'boolean_optional',step['id']+'/'+row['key'])
      self.assertEqual(actual['type'],'boolean')
      parser=argparse.ArgumentParser();parser.add_argument(*row['flags'],dest=row['key'],action=argparse.BooleanOptionalAction)
      single={**step,'arguments':[{**actual,'managed':False}]}
      for value in (True,False):
       plan={'parameters':{step['id']:{row['key']:value}},'resources':{'cpu':1},'prefix':'test','id':'test','steps':[step['id']]}
       args=adapter.command(single,plan,base,base,base,base,'/raw')[2:]
       self.assertEqual(getattr(parser.parse_args(args),row['key']),value)
       self.assertNotIn('True',args);self.assertNotIn('False',args)
      checked+=1
  self.assertGreaterEqual(checked,3)
  print('Verified optional boolean switches:',checked)

if __name__=='__main__':unittest.main()
