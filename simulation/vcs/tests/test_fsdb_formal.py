"""Sampling/selection failure modes that could silently weaken generated Formal."""
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'fsdb_formal'))
import core
import engine


class FsdbFormalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'trace.ndjson'
        self.values={s['name']:'0'*s['width'] for s in core.SIGNALS}
        self.values.update(rst_n='1',arready='111')
        self.records=[dict(type='meta',scale='1ps',dump_off='',min_time=0,max_time=40),
                      dict(type='reset',time=0,value='0'),dict(type='reset',time=7,value='1'),
                      dict(type='edge',time=10,values=copy.deepcopy(self.values)),
                      dict(type='edge',time=15,values=copy.deepcopy(self.values))]
    def tearDown(self):self.tmp.cleanup()
    def parse(self):
        self.path.write_text(''.join(json.dumps(r)+'\n' for r in self.records),encoding='utf-8')
        return core.parse_trace(self.path,dict(name='standalone.fsdb',pattern=42))

    def test_all_reset_intervals_keep_cycle_positions(self):
        self.records[3:3]=[dict(type='reset',time=17,value='0'),dict(type='reset',time=19,value='1')]
        self.records.append(dict(type='edge',time=25,values=copy.deepcopy(self.values)))
        trace=self.parse()
        self.assertEqual([s['cycles'] for s in trace['segments']],[2,1])
        self.assertEqual(trace['segments'][1]['rows'][0]['cycle'],0)
        self.assertEqual(trace['segments'][0]['rows'][1]['time'],15)
        code=core.native_sva([trace])
        self.assertIn("pattern == 32'd42 && reset_segment == 32'd1 && trace_cycle == 32'd0",code)
        self.assertEqual(code.count('cover property'),2)
        self.assertNotIn('started',code)

    def test_unknown_input_and_valid_output_are_errors(self):
        self.records[3]['values']['mem_rdata']='x'*128
        with self.assertRaisesRegex(ValueError,'输入含 X/Z'):self.parse()
        self.records[3]['values']['mem_rdata']='0'*128
        self.records[3]['values'].update(rvalid='001',rdata='x'*128)
        with self.assertRaisesRegex(ValueError,'有效输出负载含 X/Z'):self.parse()

    def test_invalid_payload_retained_but_not_asserted(self):
        self.records[3]['values'].update(rdata='x'*128,arb_addr='z'*12)
        trace=self.parse();row=trace['segments'][0]['rows'][0]
        self.assertEqual(row['values']['rdata'],'x'*128)
        self.assertNotIn('rdata',row['compared_outputs']);self.assertNotIn('arb_addr',row['compared_outputs'])
        self.assertIn('arready',row['compared_outputs'])

    def test_dump_off_and_missing_reset_fail_closed(self):
        self.records[0]['dump_off']='( 12 14 )'
        with self.assertRaisesRegex(ValueError,'dump-off'):self.parse()
        self.records[0]['dump_off']=''
        self.records.pop(1)
        with self.assertRaisesRegex(ValueError,'复位前序'):self.parse()

    def test_actual_widths_banks_and_pattern_literals(self):
        self.records[3]['values']['rd_mem_status[7]']='10'
        trace=self.parse();code=core.native_sva([trace])
        self.assertIn("rd_mem_status[7] == 2'h2",code)
        self.assertIn('input wire [1:0] rd_mem_status [7:0]',code)
        self.assertEqual(core.literal('1'+'0'*127),"128'h80000000000000000000000000000000")
        self.assertEqual(len(core.mapping('tb.cmdbuf')),31)
        with self.assertRaises(ValueError):core.mapping('tb.cmdbuf;bad')
        with self.assertRaises(ValueError):core.mapping('tb.cmdbuf',{'bogus':'x'})

    def test_native_and_sby_copies_are_separate(self):
        trace=self.parse();folder=Path(self.tmp.name)/'export'
        core.export_data(folder,[trace],'cmdbuf_rd dut (.*);')
        native=(folder/'trace_properties.sv').read_text()
        engine.sby_configs(folder,[trace],[],[],{'defines':[]})
        self.assertEqual((folder/'trace_properties.sv').read_text(),native)
        lowered=(folder/'sby_properties.sv').read_text()
        self.assertNotIn('|->',lowered)
        self.assertNotIn('$stable(',lowered)
        self.assertIn('$stable(pattern)',native)
        self.assertEqual(lowered.count('assert property'),2)
        self.assertEqual(lowered.count('assume property'),5)
        self.assertIn('sim -clock clk -resetn rst_n',(folder/'run_bmc.sby').read_text())
        self.assertNotIn('-zinit',(folder/'run_bmc.sby').read_text())

    def test_all_writing_styles_preserve_samples_and_output_validity(self):
        self.records[3]['values'].update(rdata='x'*128, arb_addr='z'*12)
        self.records[4]['values'].update(rvalid='001', req='00000001', rdata='1'*128, arb_addr='1'*12)
        trace = self.parse()
        folder = Path(self.tmp.name)/'styles'
        common = None
        self.assertEqual(len(core.FORMATS), 10)
        for style in core.FORMATS:
            with self.subTest(style=style):
                export = folder/style
                counts = core.export_data(export, [trace], 'cmdbuf_rd dut (.*);', style)
                native = (export/'trace_properties.sv').read_text()
                artifacts = [(export/name).read_bytes() for name in ('samples.csv', 'trace.json', 'transactions.json')]
                if common is None:
                    common = artifacts
                self.assertEqual(artifacts, common)
                self.assertIn("pattern == 32'd42 && reset_segment == 32'd0", native)
                self.assertEqual(counts['terminal_covers'], 1)
                engine.sby_configs(export, [trace], [], [], {'defines': []}, style)
                lowered = (export/'sby_properties.sv').read_text()
                self.assertNotIn('|->', lowered)
                lowered_count = 1 if style == 'generate' else counts['output_assertions']
                self.assertEqual(lowered.count(': assert property'), lowered_count)
                lowered_count = 1 if style == 'generate' else counts['input_assumptions']
                self.assertEqual(lowered.count(': assume property'), lowered_count+3)
                if style == 'signal':
                    self.assertNotIn('A_P42_S0_C00000_rdata:', native)
                    self.assertIn('A_P42_S0_C00001_rdata:', native)
                    self.assertIn('M_P42_S0_C00001_rd_mem_status_7:', native)
                    self.assertEqual(counts['input_assumptions'], len(core.INPUTS)*2)
                if style == 'table':
                    self.assertEqual(counts['output_assertions'], 1)
                    output_signals = [s['name'] for s in core.OUTPUTS]
                    masks = re.findall(r"output_mask_P42_S0 = \d+'h([0-9A-F]+);", native)
                    for row, packed in zip(trace['segments'][0]['rows'], masks):
                        expected = ''.join(('1' if s['name'] in row['compared_outputs'] else '0')*s['width']
                                           for s in core.OUTPUTS)
                        self.assertEqual(int(packed, 16), int(expected, 2))
                    self.assertEqual(len(masks), 2)
                    self.assertIn('trace_outputs = {'+', '.join(output_signals)+'}', native)
                    self.assertIn("trace_cycle < 32'd2", native)
                if style == 'range':
                    self.assertIn("trace_cycle >= 32'd0 && trace_cycle < 32'd2", native)
                    self.assertNotIn('A_P42_S0_rdata_C00000:', native)
                    self.assertIn('A_P42_S0_rdata_C00001:', native)
                if style == 'property':
                    self.assertIn('property trace_equals(', native)
                    self.assertNotIn('trace_equals(', lowered)


if __name__=='__main__':unittest.main()
