import os
import unittest
from pathlib import Path
from unittest.mock import patch
import protected_paths
import server_workspace as storage
import server_management_test as fixture

r=fixture.r

class ProtectedPathsTests(unittest.TestCase):
    def setUp(self):
        fixture.ManagementTests.setUp(self)
        # Windows fstat ctime differs from stat; remote runtime is Linux.
        if os.name=='nt':
            import server_file_ops
            original=server_file_ops.signature
            def signature(info):
                value=original(info);value.pop('ctime_ns',None);return value
            self.patches.append(patch.object(server_file_ops,'signature',signature));self.patches[-1].start()
    tearDown=fixture.ManagementTests.tearDown
    call=fixture.ManagementTests.call

    def protect(self,path):
        return self.call('protected_path_add',{'path':str(path)},'user')

    def test_registry_persists_and_agent_cannot_change_it(self):
        self.protect(self.data)
        self.assertEqual(self.call('protected_paths')[0]['path'],str(self.data))
        for action in ('protected_path_add','protected_path_remove'):
            with self.assertRaises(ValueError):self.call(action,{'path':str(self.data)})
        self.assertEqual(len(self.call('protected_paths')),1)
        self.call('protected_path_remove',{'path':str(self.data)},'user')
        self.assertEqual(self.call('protected_paths'),[])

    def test_write_copy_and_parent_delete_are_blocked(self):
        self.protect(self.data)
        for action,body in [('server_write',{'path':str(self.data/'new.txt'),'content':'bad'}),
                            ('server_write',{'path':str(self.data/'original.txt'),'content':'bad'}),
                            ('server_copy',{'source':str(self.data/'original.txt'),'path':str(self.data/'copy.txt')}),
                            ('server_delete',{'path':str(self.data),'recursive':True}),
                            ('server_delete',{'path':str(self.base),'recursive':True})]:
            with self.assertRaisesRegex(ValueError,'原始数据保护'):self.call(action,body)
        self.assertEqual((self.data/'original.txt').read_text(),'keep original')
        self.assertFalse((self.data/'new.txt').exists())

    def test_read_copy_out_overwrite_backup_delete_and_restore(self):
        self.protect(self.data)
        self.assertEqual(self.call('server_files',{'path':str(self.data/'original.txt'),'operation':'read'})['text'],'keep original')
        target=self.base/'copy.txt'
        self.call('server_copy',{'source':str(self.data/'original.txt'),'path':str(target)})
        result=self.call('server_write',{'path':str(target),'content':'changed'})
        self.assertEqual(Path(result['backup']).read_text(),'keep original')
        self.assertEqual(target.read_text(),'changed')
        deleted=self.call('server_delete',{'path':str(target)})
        self.assertTrue(deleted['recoverable']);self.assertFalse(target.exists())
        self.call('workspace_restore',{'trashId':deleted['trashId']})
        self.assertEqual(target.read_text(),'changed')

    def test_symlink_and_hardlink_aliases(self):
        self.protect(self.data)
        link=self.base/'alias.txt'
        os.link(self.data/'original.txt',link)
        with self.assertRaisesRegex(ValueError,'原始数据保护'):self.call('server_write',{'path':str(link),'content':'bad'})
        symlink=self.base/'linked'
        try:symlink.symlink_to(self.data,target_is_directory=True)
        except OSError as error:self.skipTest(str(error))
        with self.assertRaisesRegex(ValueError,'原始数据保护'):self.call('server_write',{'path':str(symlink/'new.txt'),'content':'bad'})

    def test_workspace_move_mkdir_config_script_and_audit(self):
        work=self.base/'workspace'
        with patch.object(r,'workspace_root',return_value=work):
            storage.workspace(r)
            folder=work/'files/raw';folder.mkdir();self.protect(folder)
            operations=[('workspace_files',{'operation':'mkdir','path':'files/raw/child'}),
                        ('workspace_files',{'operation':'move','path':'files/raw','destination':'files/moved'}),
                        ('workspace_config',{'path':'files/raw/a.json','content':{}}),
                        ('workspace_script',{'path':'files/raw/a.py','content':'print(1)'})]
            for action,body in operations:
                with self.assertRaisesRegex(ValueError,'原始数据保护'):self.call(action,body)
        actions=[row['action'] for row in r.database().execute('SELECT action FROM audit')]
        self.assertIn('protection.added',actions);self.assertEqual(actions.count('protection.blocked'),4)

if __name__=='__main__':unittest.main()
