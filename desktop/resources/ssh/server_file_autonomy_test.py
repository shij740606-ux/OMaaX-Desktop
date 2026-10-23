"""All mutations use disposable fixtures; never test permission removals on user paths."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import server_delete as deletion
import server_file_ops as writes
import server_workspace as storage
from protected_paths_test import ProtectedPathsTests


class ServerFileAutonomyTests(unittest.TestCase):
    setUp=ProtectedPathsTests.setUp
    tearDown=ProtectedPathsTests.tearDown
    call=ProtectedPathsTests.call
    protect=ProtectedPathsTests.protect

    def test_unregistered_names_running_files_and_large_text_are_allowed(self):
        content='x'*(1048576+1)
        with patch.object(Path,'home',return_value=self.base),patch.object(storage,'busy_path',return_value=True):
            for p in (self.base/'.ssh/config',self.control/'ordinary-config',self.data/'.env'):
                self.call('server_write',{'path':str(p),'content':content})
                self.assertEqual(p.stat().st_size,len(content))
                result=self.call('server_delete',{'path':str(p)})
                self.assertFalse(p.exists());self.assertTrue(result['recoverable'])
                self.call('workspace_restore',{'trashId':result['trashId']})
                self.assertEqual(p.read_text(),content)

    def test_protected_directory_can_be_copied_out_and_destination_is_backed_up(self):
        self.protect(self.data)
        target=self.base/'derived'
        self.call('server_copy',{'source':str(self.data),'path':str(target)})
        self.assertEqual((target/'original.txt').read_text(),'keep original')
        (target/'previous-only.txt').write_text('retain in backup')
        result=self.call('server_copy',{'source':str(self.data),'path':str(target)})
        self.assertFalse((target/'previous-only.txt').exists())
        self.assertEqual((Path(result['backup'])/'previous-only.txt').read_text(),'retain in backup')
        self.assertEqual((self.data/'original.txt').read_text(),'keep original')

    def test_directory_copy_preserves_links_without_changing_their_targets(self):
        link=self.data/'reference'
        try:link.symlink_to(self.data/'original.txt')
        except OSError as error:self.skipTest(str(error))
        self.protect(self.data)
        target=self.base/'copied'
        self.call('server_copy',{'source':str(self.data),'path':str(target)})
        self.assertTrue((target/'reference').is_symlink())
        self.assertEqual(os.readlink(target/'reference'),os.readlink(link))
        with self.assertRaisesRegex(ValueError,'原始数据保护'):
            self.call('server_write',{'path':str(target/'reference'),'content':'no'})

    def test_recursive_copy_cannot_replace_parent_of_registered_path_or_hardlink(self):
        target=self.base/'derived';target.mkdir();protected=target/'original';protected.mkdir();self.protect(protected)
        with self.assertRaisesRegex(ValueError,'原始数据保护'):
            self.call('server_copy',{'source':str(self.data),'path':str(target)})
        self.protect(self.data)
        alias=self.base/'aliases';alias.mkdir();os.link(self.data/'original.txt',alias/'hardlink')
        for action,body in [('server_copy',{'source':str(self.data),'path':str(alias)}),('server_delete',{'path':str(alias),'recursive':True})]:
            with self.assertRaisesRegex(ValueError,'原始数据保护'):self.call(action,body)
        self.assertEqual((alias/'hardlink').read_text(),'keep original')

    def test_copy_failure_restores_existing_directory(self):
        target=self.base/'derived';target.mkdir();(target/'old.txt').write_text('old')
        rename=Path.rename
        def fail_commit(path,destination):
            if path.name=='content' and path.parent.name.startswith('.omaax-copy-'):raise OSError('copy commit failed')
            return rename(path,destination)
        with patch.object(Path,'rename',fail_commit):
            with self.assertRaisesRegex(OSError,'copy commit failed'):
                self.call('server_copy',{'source':str(self.data),'path':str(target)})
        self.assertEqual((target/'old.txt').read_text(),'old')
        self.assertFalse((target/'original.txt').exists())
        self.assertEqual(self.call('server_write_requests',actor='user'),[])

    def test_copy_changed_source_does_not_replace_destination(self):
        target=self.base/'derived';target.mkdir();(target/'old.txt').write_text('old');copytree=writes.shutil.copytree
        def copy_then_change(*args,**kwargs):
            result=copytree(*args,**kwargs)
            (self.data/'original.txt').write_text('concurrent edit')
            return result
        with patch.object(writes.shutil,'copytree',copy_then_change):
            with self.assertRaisesRegex(ValueError,'发生变化'):
                self.call('server_copy',{'source':str(self.data),'path':str(target)})
        self.assertEqual((target/'old.txt').read_text(),'old')

    def test_parent_glob_and_duplicate_descendants_are_normalized(self):
        for name in ('one','two'):
            p=self.data/name;p.mkdir();(p/'test.tmp').write_text(name)
        result=self.call('server_delete',{'path':str(self.data/'*'/'*.tmp')})
        self.assertEqual(len(result['deleted']),2)
        self.assertEqual((self.data/'original.txt').read_text(),'keep original')
        nested=self.data/'nested';(nested/'child').mkdir(parents=True);(nested/'child/file').write_text('nested')
        result=self.call('server_delete',{'path':str(nested/'**'),'recursive':True})
        self.assertEqual(result['deleted'],[str(nested)])

    def test_batch_rename_failure_restores_already_moved_files(self):
        first=self.data/'a.tmp';first.write_text('a');second=self.data/'b.tmp';second.write_text('b');rename=Path.rename
        def fail_second(path,destination):
            if path==second:raise OSError('move failed')
            return rename(path,destination)
        with patch.object(Path,'rename',fail_second):
            with self.assertRaisesRegex(OSError,'move failed'):
                self.call('server_delete',{'path':str(self.data/'*.tmp')})
        self.assertEqual(first.read_text(),'a');self.assertEqual(second.read_text(),'b')
        self.assertEqual(self.call('server_delete_requests',actor='user'),[])

    def test_more_than_500_matching_files_can_be_deleted_with_one_audit(self):
        for number in range(501):(self.data/(str(number)+'.tmp')).write_text('fixture')
        result=self.call('server_delete',{'path':str(self.data/'*.tmp')})
        self.assertEqual(len(result['deleted']),501)
        self.assertEqual((self.data/'original.txt').read_text(),'keep original')
        self.assertEqual(len(result['trashIds']),501)

    def test_delete_registered_symlink_and_ancestor_preserves_original(self):
        self.protect(self.data)
        link=self.base/'linked'
        try:link.symlink_to(self.data,target_is_directory=True)
        except OSError as error:self.skipTest(str(error))
        for path in (self.data,self.base,link):
            with self.assertRaisesRegex(ValueError,'原始数据保护'):
                self.call('server_delete',{'path':str(path),'recursive':True})
        self.assertEqual((self.data/'original.txt').read_text(),'keep original')


if __name__=='__main__':unittest.main()
