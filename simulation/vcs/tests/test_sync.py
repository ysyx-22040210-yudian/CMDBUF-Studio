"""Synchronization must not silently discard VM-authored cases or edits."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if os.name == 'posix':
    from sync_workspace import sync


@unittest.skipUnless(os.name == 'posix', 'VM synchronization uses POSIX flock')
class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source'
        self.root = Path(self.temp.name) / 'vm'
        self.source.mkdir()
        (self.source / 'Makefile').write_text('original\n')
        sync(self.source, self.root)
        self.work = self.root / 'workspace'

    def test_preserves_vm_edit_and_new_case(self):
        (self.work / 'Makefile').write_text('VM edit\n')
        (self.work / 'new_case.json').write_text('{}')
        sync(self.source, self.root)
        self.assertEqual('VM edit\n', (self.work / 'Makefile').read_text())
        self.assertTrue((self.work / 'new_case.json').exists())

    def test_conflict_stops_before_any_source_copy(self):
        (self.work / 'Makefile').write_text('VM edit\n')
        (self.source / 'Makefile').write_text('Windows edit\n')
        (self.source / 'a_new_file').write_text('must not be copied')
        with self.assertRaises(ValueError):
            sync(self.source, self.root)
        self.assertFalse((self.work / 'a_new_file').exists())
        self.assertEqual('VM edit\n', (self.work / 'Makefile').read_text())

    def test_updates_unchanged_files_and_retains_modified_removed_file(self):
        (self.source / 'Makefile').write_text('new version\n')
        (self.source / 'remove_me').write_text('tracked\n')
        sync(self.source, self.root)
        self.assertEqual('new version\n', (self.work / 'Makefile').read_text())
        (self.work / 'remove_me').write_text('VM edit\n')
        (self.source / 'remove_me').unlink()
        sync(self.source, self.root)
        self.assertEqual('VM edit\n', (self.work / 'remove_me').read_text())


if __name__ == '__main__':
    unittest.main()
