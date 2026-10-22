import importlib.util,json,unittest
from pathlib import Path

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('engine',HERE/'workflow_engine.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
w.CATALOG=json.loads((HERE.parent/'workflows/catalog.json').read_text(encoding='utf-8'))

class ParameterCompatibility(unittest.TestCase):
    def setUp(self):
        self.flow=w.flow_for('gene_family')
        self.step=next(s for s in self.flow['nodes']+self.flow['extraNodes'] if s['id']=='gene_family_report')
    def resolve(self,params=None,config=None):
        return w.resolve_parameters(self.flow,[self.step],{'parameters':{'gene_family_report':params or {}},'configs':{'gene_family_report':config}})
    def test_empty_config(self):
        self.assertEqual(self.resolve(config={}),self.resolve())
    def test_config_to_cli_and_alias(self):
        self.assertEqual(self.resolve(config={'--scope-id':'rice','no-model':True}),self.resolve({'scope_id':'rice','no_model':True}))
    def test_conflicts_are_not_silently_discarded(self):
        with self.assertRaisesRegex(ValueError,'scope_id.*不一致'):self.resolve({'scope_id':'a'},{'scope-id':'b'})
        self.assertEqual(self.resolve({'scope_id':'a'},{'scope-id':'a'}),self.resolve({'scope_id':'a'}))
    def test_specific_unknown_field(self):
        with self.assertRaisesRegex(ValueError,'gene_family_report.*unexpected.*允许参数'):self.resolve(config={'unexpected':1})
    def test_validation_still_applies(self):
        for bad in [{'script':'echo hello'},{'scope_id':'x; rm -rf /'},{'no_model':'true'},{'workspace':'/raw'}]:
            with self.assertRaises(ValueError):self.resolve(config=bad)
    def test_omics_config_in_parameters(self):
        step={'id':'example','kind':'omics','arguments':[{'key':'config','managed':False,'flags':['--config']}],'configSchema':{'properties':{'input':{}}}}
        self.assertEqual(w.normalize_step_parameters(step,{'input':'data.csv'},{'--config':'samples.yml'}),({'config':'samples.yml'},{'input':'data.csv'}))
    def test_does_not_mutate_request(self):
        config={'scope-id':'rice'};self.resolve(config=config)
        self.assertEqual(config,{'scope-id':'rice'})

if __name__=='__main__':unittest.main()
