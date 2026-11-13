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
from types import SimpleNamespace

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/'gui'))
import backend
import formal_model
from fsdb_formal.engine import fsdb_path


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

    def test_regression_allows_source_edits_after_preparation(self):
        jobs = backend.Jobs()
        jobs.current = dict(kind='regress', state='RUNNING')
        with self.assertRaises(ValueError):
            jobs.require_editable_sources()
        jobs.current['sources_ready'] = True
        jobs.require_editable_sources()
        with self.assertRaises(ValueError):
            jobs.require_idle()

    def test_queued_cases_keep_their_source_and_report_hashes(self):
        eda = backend.eda
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'tc.sv'
            original = 'class tc extends cmdbuf_main_tc; endclass\n'
            source.write_text(original, encoding='utf-8')
            original_hash = eda.digest(source)
            build = root/'build'
            dut = dict(name='original DUT')
            eda.write_json(build/'build.json', dict(dut=dut))
            archived = build/'checker'/'tc_sources'/'tc.sv'
            archived.parent.mkdir(parents=True)
            archived.write_bytes(source.read_bytes())
            args = SimpleNamespace(command='regress', plan=None, cases='tc', seeds='2',
                                   random_case='tc', cycles=64, random_only=False, jobs=1,
                                   timeout=0, waves=0)

            def run_case(build_dir, regression, name, seed, cycles, env, timeout, injection,
                         prepared_case, **policy):
                case_name, cfg = prepared_case
                self.assertEqual(cfg['source'], original)
                if seed == 1:
                    source.write_text('new workspace draft', encoding='utf-8')
                return dict(id='{}_seed_{}'.format(case_name, seed), status='PASS',
                            seconds=0, metrics={}, wave_artifacts=[])

            with patch.object(eda, 'OUT', root), patch.object(eda, 'case_path', return_value=source), \
                    patch.object(eda, 'build_workspace', return_value=(build, {'tc.sv': original_hash})), \
                    patch.object(eda, 'run_case', side_effect=run_case), \
                    patch.object(eda, 'merge_coverage', return_value=dict(status='PASS')), \
                    patch.object(eda, 'write_report', side_effect=lambda folder, summary:
                                      eda.write_json(folder/'summary.json', summary)):
                self.assertEqual(eda.regress(args, {}), 0)
            batch = eda.read_json(root/'latest.json')['id']
            summary = eda.read_json(root/'regressions'/batch/'summary.json')
            self.assertEqual(summary['passed'], 2)
            self.assertEqual(summary['dut'], dut)
            self.assertEqual(summary['execution_sources'], {'tc.sv': original_hash})
            self.assertNotEqual(eda.digest(source), original_hash)

    def test_native_fsdb_import_keeps_exact_bytes_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);src=base/'source.fsdb';src.write_bytes(bytes(range(256))*4096)
            with patch.object(formal_model,'IMPORTS',base/'imports'):
                meta=formal_model.import_file(src)
                target=formal_model.IMPORTS/meta['id']/'wave.fsdb'
                self.assertTrue(meta['ready']);self.assertEqual(src.read_bytes(),target.read_bytes())
                self.assertEqual(meta['sha256'],formal_model.file_hash(src))

    def test_formal_regression_source_survives_result_root_change(self):
        eda = backend.eda
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp)/'external'
            current = Path(tmp)/'default'
            batch, run = 'batch_1', 'tc_seed_1'
            wave = original/'regressions'/batch/'cases'/run/'waves.fsdb'
            wave.parent.mkdir(parents=True)
            wave.touch()
            summary = dict(id=batch, results=[dict(id=run, waves_enabled=True)])
            eda.write_json(wave.parents[2]/'summary.json', summary)
            item = dict(kind='regression', batch=batch, run=run, name=run, pattern=7)
            with patch.object(eda, 'OUT', original):
                selection = formal_model.selections(dict(sources=[item]))
            saved, = selection['sources']
            self.assertEqual(saved['archive'], str(wave.parents[2].resolve()))

            # A different results directory may even contain the same batch ID.
            other = current/'regressions'/batch/'cases'/run/'waves.fsdb'
            other.parent.mkdir(parents=True)
            other.touch()
            eda.write_json(other.parents[2]/'summary.json', summary)
            with patch.object(eda, 'OUT', current):
                self.assertEqual(formal_model.source_path(saved), wave.resolve())
                self.assertEqual(fsdb_path(saved), wave.resolve())
                self.assertEqual(fsdb_path(item), other)
                wave.unlink()
                with self.assertRaisesRegex(ValueError, '没有可读取的 FSDB'):
                    fsdb_path(saved)
                (wave.parents[2]/'summary.json').unlink()
                with self.assertRaisesRegex(ValueError, '从“结果与波形”重新加入'):
                    formal_model.selections(selection)

    def test_partial_results_use_committed_rows_and_retain_interrupted_batch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(backend.model, 'results_root', return_value=Path(tmp)):
            batch = 'batch_1'
            folder = backend.batch_path(batch)
            first = dict(id='tc_seed_1', status='PASS', seconds=0.5, cycles=64, waves=True)
            second = dict(id='tc_seed_2', status='FAIL', seconds=0.6, cycles=64, waves=False)
            progress = dict(id=batch, state='RUNNING', total=3, completed=1, passed=1,
                            dut='cmdbuf', waves_enabled=True)
            backend.eda.write_json(folder/'progress.json', progress)
            run = folder/'cases'/first['id']
            backend.eda.write_json(run/'result.json', first)
            (folder/'cases'/second['id']).mkdir()
            journal = folder/'completed_runs.jsonl'
            journal.write_text(json.dumps(first)+'\n'+json.dumps(second)+'\n', encoding='utf-8')
            row = backend.progress_row(batch)
            rows, offset = backend.completed_runs(batch, 0, row['completed'])
            self.assertEqual(rows, [first])
            self.assertEqual(backend.result_path(batch, first['id']), run)
            with self.assertRaises(ValueError):
                backend.result_path(batch, second['id'])
            self.assertEqual(backend.history(include_interrupted=True), [])
            backend.eda.write_json(folder/'progress.json', dict(progress, state='ERROR', completed=2))
            row, = backend.history(include_interrupted=True)
            rows, _ = backend.completed_runs(batch, offset, row['completed']-1)
            self.assertEqual(rows, [second])
            self.assertTrue(row['partial'])
            self.assertEqual((row['completed'], row['status']), (2, 'ERROR'))
            self.assertEqual(backend.history(), [])

    def test_no_gui_web_or_ssh_runtime(self):
        for path in list((HERE/'gui').glob('*.py'))+[HERE/'launcher.py']:
            source=path.read_text(encoding='utf-8')
            self.assertNotIn('import webbrowser',source)
            self.assertNotIn('HTTPServer',source)
            self.assertNotIn('eda.ssh(',source)
        make=(HERE/'Makefile').read_text(encoding='utf-8')
        self.assertIn('gui/desktop.py',make);self.assertNotIn('gui/server.py',make)


if __name__=='__main__':unittest.main()
