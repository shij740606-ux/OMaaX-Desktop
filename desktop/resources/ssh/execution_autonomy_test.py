import json,os,py_compile,subprocess,sys,unittest
from pathlib import Path
from unittest.mock import patch
import workflow_engine_test as fixture
import server_workspace as storage
import conda_runtime as runtime
import workflow_environments as environments
r=fixture.r

class ExecutionAutonomyTests(unittest.TestCase):
    setUp=fixture.ContractTests.setUp
    tearDown=fixture.ContractTests.tearDown

    def test_missing_conda_uses_durable_host_task_and_audit(self):
        broken='conda:'+'b'*64
        db=r.database();db.execute('INSERT OR REPLACE INTO config VALUES(?,?)',('images',json.dumps([broken])));db.commit()
        result=storage.analysis(r,{'argv':[sys.executable,'--version'],'cpu':1,'memoryMb':128,'timeoutSeconds':10})
        row=r.database().execute('SELECT spec,state FROM jobs WHERE id=?',(result['id'],)).fetchone()
        spec=json.loads(row['spec']);self.assertEqual(spec['image'],runtime.HOST_REF);self.assertEqual(row['state'],'queued')
        payload=json.loads(r.container_args(spec,r.config(r.database()))[-1]);self.assertIsNone(payload['opt'])
        self.assertEqual(payload['argv'],[sys.executable,'--version']);self.assertTrue(r.jobdir(result['id']).is_dir())
        self.assertIsNotNone(r.database().execute("SELECT 1 FROM audit WHERE action='workspace.analysis_submitted'").fetchone())

    def test_many_inputs_and_long_arguments_are_not_permission_gates(self):
        inputs=[]
        for index in range(40):
            path=self.base/('input'+str(index));path.mkdir();inputs.append(str(path))
        result=storage.analysis(r,{'image':runtime.HOST_REF,'inputs':inputs,'argv':['tool','x'*10000,*['arg']*150],'cpu':1,'memoryMb':128,'timeoutSeconds':10})
        spec=json.loads(r.database().execute('SELECT spec FROM jobs WHERE id=?',(result['id'],)).fetchone()['spec'])
        self.assertEqual(len(spec['sources']),40);self.assertEqual(len(spec['argv']),152)

    def test_host_execution_runs_real_process_with_output_mapping(self):
        output=self.base/'output';output.mkdir()
        command,environment,cwd=runtime.command(None,[sys.executable,'-c',"from pathlib import Path; Path('/output/result.txt').write_text('host-ok')"],output.as_posix())
        process=subprocess.run(command,env=environment,cwd=cwd,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual((output/'result.txt').read_text(),'host-ok')

    def test_custom_python_script_maps_output_but_preserves_data_and_local_imports(self):
        output=self.base/'custom-output';output.mkdir()
        helper=self.base/'helper.py';helper.write_text("VALUE='custom-ok'\n")
        data=self.base/'data.py';data.write_text('/output/is-data-not-code')
        script=self.base/'run.custom';text="import helper,sys\nfrom pathlib import Path\nassert Path(sys.argv[1]).read_text()=='/out'+'put/is-data-not-code'\nPath('/output/result.txt').write_text(helper.VALUE)\n";script.write_text(text)
        command,environment,cwd=runtime.command(None,[sys.executable,'-u',script.as_posix(),data.as_posix()],output.as_posix())
        process=subprocess.run(command,env=environment,cwd=cwd,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual((output/'result.txt').read_text(),'custom-ok')
        self.assertEqual(script.read_text(),text);self.assertEqual(data.read_text(),'/output/is-data-not-code');self.assertEqual(command[-1],data.as_posix())

    @unittest.skipIf(os.name=='nt','real server uses Linux shell')
    def test_custom_shell_script_maps_output_and_preserves_original(self):
        output=self.base/'shell-output';output.mkdir();script=self.base/'repair.custom'
        text="printf 'shell-ok' > /output/result.txt\n";script.write_text(text)
        command,environment,cwd=runtime.command(None,['/bin/bash','-eu',str(script)],output)
        process=subprocess.run(command,env=environment,cwd=cwd,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual((output/'result.txt').read_text(),'shell-ok');self.assertEqual(script.read_text(),text)

    def test_interpreter_detection_does_not_rewrite_data_arguments(self):
        self.assertEqual(runtime.script_argument(['python3','-u','run.custom','data.py']),2)
        self.assertEqual(runtime.script_argument(['/bin/bash','-eu','run.custom']),2)
        self.assertEqual(runtime.script_argument(['/bin/bash','-e','run.custom']),2)
        self.assertEqual(runtime.script_argument(['python3','-O','run.custom','data.py']),2)
        self.assertIsNone(runtime.script_argument(['/bin/bash','-lc','cat data.py']))
        self.assertIsNone(runtime.script_argument(['python3','-m','module','data.py']))
        self.assertIsNone(runtime.script_argument(['cp','data.py','other.py']))
        self.assertEqual(runtime.script_argument(['Rscript','--encoding','latin1','run.custom']),3)

    def test_compiled_python_script_runs_without_decoding_binary(self):
        output=self.base/'compiled-output';output.mkdir();source=self.base/'compiled.py'
        source.write_text("print('compiled-ok')\n");compiled=self.base/'compiled.pyc';py_compile.compile(str(source),cfile=str(compiled),doraise=True)
        original=compiled.read_bytes()
        command,environment,cwd=runtime.command(None,[sys.executable,compiled.as_posix()],output.as_posix())
        process=subprocess.run(command,env=environment,cwd=cwd,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual(process.stdout.strip(),'compiled-ok');self.assertEqual(compiled.read_bytes(),original)

    def test_python_declared_encoding_survives_output_mapping(self):
        output=self.base/'latin-output';output.mkdir();script=self.base/'latin.custom'
        original="# coding: latin-1\nfrom pathlib import Path\nPath('/output/result.txt').write_text('café',encoding='utf-8')\n".encode('latin-1');script.write_bytes(original)
        command,environment,cwd=runtime.command(None,[sys.executable,script.as_posix()],output.as_posix())
        process=subprocess.run(command,env=environment,cwd=cwd,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual((output/'result.txt').read_text(encoding='utf-8'),'café');self.assertEqual(script.read_bytes(),original)

    def test_rscript_explicit_encoding_is_not_mistaken_for_script(self):
        output=self.base/'r-output';output.mkdir();script=self.base/'plot.custom';original="writeLines('café', '/output/result.txt')\n".encode('latin-1');script.write_bytes(original)
        with patch.object(runtime.shutil,'which',return_value='Rscript'):
            command,_,_=runtime.command(None,['Rscript','--encoding','latin1',script.as_posix()],output.as_posix())
        self.assertEqual(command[1:3],['--encoding','latin1']);self.assertIn('café',Path(command[3]).read_text(encoding='latin-1'));self.assertIn(output.as_posix(),Path(command[3]).read_text(encoding='latin-1'));self.assertEqual(script.read_bytes(),original)

    def test_env_launcher_preserves_pythonpath_even_when_parent_value_matches(self):
        with patch.dict(os.environ,{'PYTHONPATH':'same-import-path'}),patch.object(runtime,'command',return_value=(['python3'],{'PYTHONPATH':'same-import-path'},'/unused')):
            command=runtime.args(None,runtime.HOST_REF,['python3'])
        self.assertIn('PYTHONPATH=same-import-path',command)

    def test_host_runner_cannot_substitute_for_formal_workflow(self):
        spec={'id':'test','image':runtime.HOST_REF,'argv':['true'],'workflow':{'id':'rnaseq'},'cpu':1,'memoryMb':128,'timeoutSeconds':10}
        with self.assertRaisesRegex(ValueError,'正式分析'):r.validate_job(spec,r.config(r.database()))

    def test_script_arbitrary_extension_size_and_absolute_path(self):
        target=self.base/'outside'/'tool.custom';content='# text\n'+'x'*1048577
        result=storage.script(r,{'path':str(target),'content':content})
        self.assertEqual(target.read_text(),content);self.assertEqual(result['path'],str(target))
        config=storage.write_config(r,{'path':str(self.base/'outside'/'config.custom'),'content':{'setting':True}})
        self.assertEqual(json.loads(Path(config['path']).read_text()),{'setting':True})

    def test_root_account_and_large_repair_script_are_not_rejected(self):
        with patch.object(runtime.os,'getuid',return_value=0,create=True):
            self.assertEqual(runtime.protection()['uid'],0)
        recipe={'stage':{'script':'# '+'x'*140000,'packages':['x']*300}}
        self.assertEqual(environments.validate_recipes(recipe,[{'id':'stage'}]),recipe)

    @unittest.skipIf(os.name=='nt','symbolic link permissions differ on Windows; run on Linux acceptance')
    def test_directory_inputs_may_contain_links(self):
        link=self.data/'linked';link.symlink_to(self.base/'outside')
        (self.base/'outside').mkdir();(self.base/'outside'/'data.txt').write_text('data')
        result=storage.analysis(r,{'inputs':[str(self.data)],'argv':['true'],'cpu':1,'memoryMb':128,'timeoutSeconds':10})
        self.assertEqual(result['state'],'queued')

if __name__=='__main__':unittest.main()
