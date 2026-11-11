import copy
import re
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import case_adapter as adapter
import eda


class AdapterTests(unittest.TestCase):
    def test_seed_reproduction_and_legal_ranges(self):
        cfg = {'sequence': [{'random': 100}]}
        a = adapter.make_program(cfg, 42)
        self.assertEqual(a, adapter.make_program(cfg, 42))
        self.assertNotEqual(a, adapter.make_program(cfg, 43))
        self.assertEqual(['2','100','0','42']+['0']*9,a.split())
        # Pin values must be solved with runtime feedback, never precomputed.
        self.assertEqual(1,len(a.splitlines()))
        self.assertEqual('23',adapter.make_program(cfg,1,cycles=23).split()[1])

    def test_directed_environment_and_wait_handshake(self):
        cfg = {'sequence': [{'set': {'status': '0x0101', 'rwgrant': 0}},
                            {'send': {'src': 2, 'addr': '0x12345'}, 'timeout': 29},
                            {'wait': 7}, {'set': {'rwgrant': 255}},
                            {'cycle': {'src': 0, 'addr': 10}}]}
        rows = [r.split() for r in adapter.make_program(cfg, 1).splitlines()]
        self.assertEqual(rows[0][:4], ['1', '29', '1', '2'])
        self.assertEqual(rows[0][4], '12345')
        self.assertEqual(rows[0][9:12], ['101', 'ff', '0'])
        self.assertEqual(rows[1][:3], ['0', '7', '0'])
        self.assertEqual(rows[2][11], 'ff')

    def test_reject_invalid_ports_and_typographical_errors(self):
        for field, value in [('src', 3), ('resp', 1), ('addr', 1 << 18),
                             ('port', 32), ('id', -1), ('src', True), ('adress', 1)]:
            cfg = {'sequence': [{'send': {'src': 0, 'addr': 0}}]}
            cfg['sequence'][0]['send'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                adapter.make_program(cfg, 1)
        for cfg in ({'sequence': [{'wait': 0}]}, {'sequence': [{'set': {'status': 65536}}]},
                    {'sequence': [{'send': {'src': 0}}]}, {'sequence': [{'wait': 1, 'random': 2}]}):
            with self.assertRaises(ValueError):
                adapter.make_program(cfg, 1)

    def test_instrumentation_preserves_every_property_expression(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            eda.instrument_checker(target)
            records = eda.read_json(target / 'instrumentation.json')
            self.assertGreater(sum(len(r['properties']) for r in records), 30)
            for record in records:
                original = (eda.ROOT / record['source']).read_text(encoding='utf-8')
                revised = (target / Path(record['source']).name).read_text(encoding='utf-8')
                revised = re.sub(r' else tb_cmdbuf_rd\.monitor\.native_failure\([^\n]+\);', ';', revised)
                revised = revised.replace('disable iff (!rst_n || !tb_cmdbuf_rd.monitor.strict_mode)', 'disable iff (!rst_n)')
                for prop in record['properties']:
                    if prop['original_kind'] == 'assume':
                        revised = revised.replace(prop['label'] + ': assert', prop['label'] + ': assume')
                self.assertEqual(original, revised)


if __name__ == '__main__':
    unittest.main()
