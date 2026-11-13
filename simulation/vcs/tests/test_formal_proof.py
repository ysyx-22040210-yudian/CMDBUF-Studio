"""Guard against false proof passes and mismatched archived inputs."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'fsdb_formal'))
import proof


class FormalProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve()
        self.package = self.path/'package'
        self.package.mkdir()
        summary = dict(id='fixture', patterns=[dict(pattern=17, segments=[dict(segment=0)])],
                       counts=dict(terminal_covers=1), dut_name='Test DUT')
        values = {
            'summary.json': json.dumps(summary),
            'integration.json': json.dumps(dict(depth=6)),
            'sby_properties.sv': 'C_END_P17_S0: cover property (1);\nA: assert property (1);',
            'rtl_snapshot/rtl/dut.sv': 'module dut; endmodule',
        }
        manifest = {}
        for name in list(proof.INPUTS) + ['rtl_snapshot/rtl/dut.sv']:
            file = self.package/name
            file.parent.mkdir(parents=True, exist_ok=True)
            data = values.get(name, '').encode('utf-8')
            file.write_bytes(data)
            manifest[name] = hashlib.sha256(data).hexdigest()
        (self.package/'SHA256SUMS.json').write_text(json.dumps(manifest), encoding='utf-8')

    def run_proof(self, responses):
        @contextmanager
        def environment():
            yield {}

        with patch.object(proof.toolchain, 'environment', environment), \
             patch.object(proof.toolchain, 'versions', return_value={}), \
             patch.object(proof, 'execute', side_effect=responses) as execute, \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            rc = proof.prove(self.package, self.path/'results', 'one')
        receipt = proof.read_json(self.path/'results/fixture/one/proof.json')
        return rc, receipt, execute.call_count

    def test_pass_requires_every_terminal_cover(self):
        rc, record, runs = self.run_proof([dict(status='PASS'), dict(status='PASS', reached=[])])
        self.assertEqual((rc, record['status'], runs), (2, 'INCOMPLETE', 2))
        self.assertEqual(record['unreached_covers'], ['C_END_P17_S0'])

    def test_bmc_failure_preserves_the_counterexample(self):
        failure = dict(status='FAIL', vcds=['bmc/engine_0/trace.vcd'])
        rc, record, runs = self.run_proof([failure])
        self.assertEqual((rc, record['status'], runs), (1, 'FAIL', 1))
        self.assertEqual(record['bmc'], failure)
        self.assertEqual(record['cover']['status'], 'NOT_RUN')

    def test_modified_rtl_snapshot_is_rejected_before_solver(self):
        (self.package/'rtl_snapshot/rtl/dut.sv').write_text('changed', encoding='utf-8')
        rc, record, runs = self.run_proof([])
        self.assertEqual((rc, record['status'], runs), (2, 'ERROR', 0))
        self.assertIn('rtl_snapshot/rtl/dut.sv', record['error'])

    def test_cover_failure_and_timeouts_are_incomplete(self):
        self.assertEqual(proof.verdict('PASS', 'FAIL', ['C_END_P17_S0']), 'INCOMPLETE')
        self.assertEqual(proof.verdict('TIMEOUT', 'NOT_RUN', ['C_END_P17_S0']), 'INCOMPLETE')


if __name__ == '__main__':
    unittest.main()
