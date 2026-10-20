import os
from pathlib import Path
import shutil
import tempfile
import unittest
import personal_runner as runner


@unittest.skipUnless(os.name == 'posix', 'Linux symlink snapshots')
class CheckpointLinks(unittest.TestCase):
    def test_native_absolute_internal_link_survives_snapshot_but_external_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output = base/'output'
            (output/'source/scripts').mkdir(parents=True)
            (output/'analysis').mkdir()
            (output/'source/scripts/tool.py').write_text('fixture')
            (output/'analysis/scripts').symlink_to(output/'source/scripts', target_is_directory=True)
            before = runner.tree_manifest(output)
            snapshot = base/'checkpoint/output'
            shutil.copytree(output, snapshot, symlinks=True)
            self.assertEqual(runner.tree_manifest(snapshot, output), before)
            (snapshot/'outside').symlink_to(base/'private')
            with self.assertRaisesRegex(ValueError, '外部链接'):
                runner.tree_manifest(snapshot, output)

    def test_mapped_target_cannot_escape_through_another_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output = base/'output'
            snapshot = base/'checkpoint/output'
            snapshot.mkdir(parents=True)
            (snapshot/'scripts').symlink_to(output/'source', target_is_directory=True)
            (snapshot/'source').symlink_to(base/'private', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, '外部链接'):
                runner.tree_manifest(snapshot, output)
