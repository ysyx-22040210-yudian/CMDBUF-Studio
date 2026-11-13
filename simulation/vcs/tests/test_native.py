"""Native dispatch boundaries and asynchronous job behavior."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/'gui'))
import backend
import formal_model


class NativeTests(unittest.TestCase):
    def test_make_arguments_are_local_and_reject_recipe_expansion(self):
        with patch.object(backend.sys,'platform','linux'),patch.object(backend.shutil,'which',return_value='/usr/bin/make'):
            cmd=backend.make_command(['wave','REGRESSION=batch_1','RUN_ID=tc_seed_7'])
            self.assertEqual(cmd[0],'make');self.assertNotIn('ssh',' '.join(cmd))
            for argument in ['FSDB=/tmp/$(shell touch bad).fsdb','FSDB=/tmp/`bad`.fsdb','PLAN=bad\npath','FSDB=/tmp/"bad']:
                with self.assertRaises(ValueError):backend.make_command(['wave-file',argument])
        with patch.object(backend.sys,'platform','win32'):
            with self.assertRaises(RuntimeError):backend.make_command(['compile'])

    def test_job_exclusion_failure_and_recovery(self):
        release=threading.Event();jobs=backend.Jobs()
        def slow(job,args,check=True):
            release.wait(3);raise RuntimeError('intentional tool failure')
        with tempfile.TemporaryDirectory() as tmp,patch.object(backend,'STATE',Path(tmp)),patch.object(backend,'make_command',return_value=['make']),patch.object(jobs,'invoke',side_effect=slow):
            jobs.start('compile')
            with self.assertRaises(ValueError):jobs.start('doctor')
            release.set()
            deadline=time.time()+4
            while jobs.snapshot()['state']=='RUNNING' and time.time()<deadline:time.sleep(.01)
            result=jobs.snapshot();self.assertEqual(result['state'],'ERROR');self.assertIn('intentional tool failure',result['log'])
            jobs.require_idle()
            # Completion log is retained for diagnosis; callers get copies.
            result['state']='tampered';self.assertEqual(jobs.snapshot()['state'],'ERROR')

    def test_native_fsdb_import_keeps_exact_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);src=base/'source.fsdb';src.write_bytes(bytes(range(256))*4096)
            with patch.object(formal_model,'IMPORTS',base/'imports'):
                meta=formal_model.import_file(src)
                target=formal_model.IMPORTS/meta['id']/'wave.fsdb'
                self.assertTrue(meta['ready']);self.assertEqual(src.read_bytes(),target.read_bytes())
                self.assertEqual(meta['sha256'],formal_model.file_hash(src))

    def test_no_gui_web_or_ssh_runtime(self):
        for path in list((HERE/'gui').glob('*.py'))+[HERE/'launcher.py']:
            source=path.read_text(encoding='utf-8')
            self.assertNotIn('import webbrowser',source)
            self.assertNotIn('HTTPServer',source)
            self.assertNotIn('eda.ssh(',source)
        make=(HERE/'Makefile').read_text(encoding='utf-8')
        self.assertIn('gui/desktop.py',make);self.assertNotIn('gui/server.py',make)


if __name__=='__main__':unittest.main()
