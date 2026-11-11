import copy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import case_adapter
import wavedrom_adapter as wave


class WaveDromTests(unittest.TestCase):
    def cfg(self, name='read'):
        path = Path(__file__).resolve().parents[1] / 'cases' / ('wavedrom_' + name + '.json')
        return json.loads(path.read_text(encoding='utf-8'))

    def test_gate_clock_counts_real_edges_and_manual_mem(self):
        plan = wave.compile_wave(self.cfg())
        self.assertEqual(list(range(5))+list(range(8,20)), [s['column'] for s in plan['samples']])
        returned = plan['samples'][8]
        self.assertEqual(11, returned['column'])
        self.assertEqual('1', returned['pc_err'])
        self.assertEqual(0x00112233445566778899aabbccddeeff, int(returned['mem_rdata'],2))
        self.assertEqual(200001, plan['frames'][-1]['time_ps'])

    def test_period_and_literal_clock_have_same_positive_edges(self):
        a = wave.compile_wave(self.cfg('nclock'))
        b = wave.compile_wave(self.cfg('levels'))
        self.assertEqual([x['time_ps'] for x in a['samples']], [x['time_ps'] for x in b['samples']])
        self.assertEqual(list(range(1,20,2)), [x['column'] for x in a['samples']])

    def test_explicit_reset_during_clock_pause_is_preserved(self):
        plan = wave.compile_wave(self.cfg('reset'))
        frame = next(f for f in plan['frames'] if f['time_ps'] == 50000)
        self.assertEqual('0', frame['rst_n'])
        self.assertEqual('0', frame['clk'])
        self.assertFalse(any(s['column'] in (5,6,7) for s in plan['samples']))

    def test_reject_ambiguous_or_missing_input_descriptions(self):
        changes = [lambda c:c['signal'].pop(),
                   lambda c:c['signal'].append(copy.deepcopy(c['signal'][0])),
                   lambda c:c['signal'][0].update(wave='.p..................'),
                   lambda c:c['signal'][0].update(wave='p....|..p...........'),
                   lambda c:c['signal'][0].update(phase=.5),
                   lambda c:c['signal'][0].update(wave='p...'),
                   lambda c:c['signal'][0].update(name='rvalid'),
                   lambda c:c['signal'][4].update(data=['address'])]
        for change in changes:
            cfg=self.cfg(); change(cfg)
            with self.assertRaises(ValueError): wave.compile_wave(cfg)
        with self.assertRaises(ValueError): case_adapter.make_program(self.cfg(), 1, cycles=10)

    def test_numeric_and_four_state_values_keep_port_width(self):
        self.assertEqual('10xz0011',wave.bits("8'b10xz0011",8,'test'))
        self.assertEqual('xxxxzzzz',wave.bits("8'hxz",8,'test'))
        self.assertEqual('1'*128,wave.bits('0x'+'f'*32,128,'test'))
        for value in (-1,256,True,"4'hf"):
            with self.assertRaises(ValueError):wave.bits(value,8,'test')

    def test_symbols_are_resolved_without_changing_diagram_labels(self):
        cfg=self.cfg()
        mem=next(s for s in cfg['signal'] if s['name']=='mem_rdata')
        self.assertIn('DATA_A',mem['data'])
        plan=wave.compile_wave(cfg)
        self.assertEqual(0x00112233445566778899aabbccddeeff,int(plan['samples'][8]['mem_rdata'],2))
        self.assertIn('DATA_A',mem['data'])
        cfg['sim']['values'].pop('DATA_A')
        with self.assertRaises(ValueError):wave.compile_wave(cfg)

    def test_audit_detects_driver_changes_and_uses_archived_program(self):
        cfg=self.cfg(); plan=wave.compile_wave(cfg); program=wave.make_program(cfg)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'samples.csv').write_text(wave.sample_csv(plan))
            with (root/'driven.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=['time_ps']+plan['input_ports'])
                writer.writeheader();writer.writerows(plan['frames'])
            self.assertEqual('PASS',wave.verify_execution({},root,program)['status'])
            text=(root/'samples.csv').read_text();rows=list(csv.DictReader(io.StringIO(text)))
            rows[2]['arvalid']='0'
            with (root/'samples.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
                writer.writeheader();writer.writerows(rows)
            self.assertEqual('FAIL',wave.verify_execution(cfg,root,program)['status'])


if __name__ == '__main__': unittest.main()
