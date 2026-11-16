"""Guard native source handoff and the commercial adapter verdict boundary."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import commercial_formal as commercial


class CommercialFormalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.eda = commercial.eda
        self.request = dict(run_id='check', targets=[], kinds=['assert', 'cover'], depth=12,
                            required_labels={'cover': ['C_END_P2_S0'], 'assert': ['A_OUTPUT']})
        self.response = dict(schema=commercial.SCHEMA, run_id='check', request_sha256='request-digest',
                             engine={'name': 'Fixture', 'version': '1.0'}, complete=True, properties=[
                                 dict(name='top.monitor.A_OUTPUT', label='A_OUTPUT', kind='assert',
                                      status='BOUNDED_PASS', depth=12),
                                 dict(name='top.monitor.C_END_P2_S0', label='C_END_P2_S0', kind='cover',
                                      status='REACHED', depth=10),
                             ])

    def normalize(self):
        return commercial.normalize(self.root, self.request, 'request-digest', self.response)

    def test_proof_requires_full_depth_and_every_fsdb_endpoint(self):
        self.assertEqual(self.normalize()['status'], 'PASS')
        self.response['properties'][0]['depth'] = 11
        self.assertEqual(self.normalize()['status'], 'INCOMPLETE')
        self.response['properties'][0]['status'] = 'VACUOUS'
        self.assertEqual(self.normalize()['status'], 'INCOMPLETE')
        self.response['properties'].pop()
        self.assertIn('C_END_P2_S0', self.normalize()['missing'])
        self.response['properties'][0]['status'] = 'COUNTEREXAMPLE'
        self.assertEqual(self.normalize()['status'], 'FAIL')

    def test_rejects_stale_answers_wrong_targets_and_external_traces(self):
        self.response['run_id'] = 'other'
        with self.assertRaisesRegex(ValueError, '运行编号'):
            self.normalize()
        self.response['run_id'] = 'check'
        self.request['targets'] = ['NOT_SELECTED']
        with self.assertRaisesRegex(ValueError, '未选中'):
            self.normalize()
        self.request['targets'].clear()
        self.response['properties'][1]['trace'] = '../escape.vcd'
        with self.assertRaisesRegex(ValueError, '路径'):
            self.normalize()

    def test_prepare_keeps_raw_complex_editor_sva(self):
        authored = self.root / 'editor.sv'
        raw = b'C_OPEN: cover property (@(posedge clk) disable iff (!rst_n) ar_fire ##[1:$] r_fire);\n'
        authored.write_bytes(raw)
        with patch.object(self.eda, 'OUT', self.root), patch.object(commercial.props, 'USER_FILE', authored), \
             redirect_stdout(StringIO()):
            rc = commercial.run('cover', 'prepare', None, 0, ['C_OPEN'], None, None, True, ['SYNTHESIS'])
        job = self.root / 'commercial_formal/prepare'
        self.assertEqual(rc, 0)
        self.assertEqual(self.eda.read_json(job / 'result.json')['status'], 'PREPARED')
        self.assertEqual((job / 'source/user_properties.sv').read_bytes(), raw)
        request = self.eda.read_json(job / 'request.json')
        self.assertEqual(request['design']['top'], commercial.TOP)
        self.assertTrue(request['design']['initialize_reset'])
        self.assertIn('test_mode_rd_port_sva.sv', request['inputs']['files'])
        self.assertIn('SYNTHESIS', request['inputs']['defines'])
        self.assertIn('+define+SYNTHESIS', (job / 'source/files.f').read_text(encoding='utf-8'))

    def test_fsdb_uses_native_sva_and_rejects_a_changed_snapshot(self):
        package = self.root / 'package'
        package.mkdir()
        payload = {
            'rtl_snapshot/DUT.sv': 'module DUT; endmodule\n',
            'files.f': 'rtl_snapshot/DUT.sv\ntrace_properties.sv\nformal_top.sv\n',
            'trace_properties.sv': 'A_OUTPUT: assert property (1);\nC_END_P2_S0: cover property (1);\n',
            'formal_top.sv': 'module my_top; endmodule\n',
        }
        for name, content in payload.items():
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        self.eda.write_json(package / 'summary.json',
                            dict(id='archived', dut_name='Mock', patterns=[dict(pattern=2, segments=[dict(segment=0)])],
                                 counts=dict(terminal_covers=1)))
        self.eda.write_json(package / 'integration.json',
                            dict(top='my_top', clock='clk', active_low_reset='rst_n', depth=12,
                                 native_checker='trace_properties.sv', filelist='files.f', selectors=['pattern']))
        self.eda.write_json(package / 'SHA256SUMS.json', {
            p.relative_to(package).as_posix(): self.eda.digest(p) for p in package.rglob('*') if p.is_file()})
        job = self.root / 'snapshot'; job.mkdir()
        request = commercial.build_request(job, 'prove', None, 10, [], package)
        self.assertEqual(request['required_labels'], self.request['required_labels'])
        self.assertEqual(request['depth'], 12)
        self.assertNotIn('sby_properties.sv', request['inputs']['files'])
        (package / 'trace_properties.sv').write_text('changed', encoding='utf-8')
        job = self.root / 'changed'; job.mkdir()
        with self.assertRaisesRegex(ValueError, '校验失败'):
            commercial.build_request(job, 'prove', None, 10, [], package)

    def test_adapter_input_mutation_cannot_produce_a_pass(self):
        def adapter(argv, directory, log, env, limit, phase):
            self.assertIsNone(limit)
            (directory / 'source/user_properties.sv').write_text('changed', encoding='utf-8')
            return 0, 0

        with patch.object(self.eda, 'OUT', self.root), patch.object(commercial.sys, 'platform', 'linux'), \
             patch.object(commercial.shutil, 'which', return_value='/site/fixture'), \
             patch.object(self.eda.platform_config, 'load', return_value={'tool_setup': '', 'formal_adapter': ''}), \
             patch.object(self.eda, 'execute', side_effect=adapter), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            rc = commercial.run('cover', 'mutation', 12, 10, [], None, '/site/fixture', False)
        record = self.eda.read_json(self.root / 'commercial_formal/mutation/result.json')
        self.assertEqual((rc, record['status']), (2, 'ERROR'))
        self.assertIn('修改了请求或原生输入', record['error'])


if __name__ == '__main__':
    unittest.main()
