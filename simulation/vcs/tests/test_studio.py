"""Meaningful authoring, plan and waveform invariants for the desktop UI."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE/'gui'))
import model
import vcd
import regression_plan
import dut_config


class StudioTests(unittest.TestCase):
    def test_extract_phase_ignores_keywords_in_comments_and_strings(self):
        source='class c; // task main_phase; endtask\nvirtual task main_phase;\n $display("endtask");\n /* endtask */\nendtask\nendclass'
        a,b=model.phase_span(source)
        self.assertEqual(source[a:b],'virtual task main_phase;\n $display("endtask");\n /* endtask */\nendtask')
        model.check_phase(source[a:b])
        with self.assertRaises(ValueError):model.check_phase(source[a:b]+'\nendclass')

    def test_save_preserves_constructor_and_rejects_external_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tc=root/'simulation/vcs/tc';tc.mkdir(parents=True)
            path=tc/'test_read.sv'
            before='class test_read extends cmdbuf_main_tc;\nfunction new(); num_cycles=91; endfunction\nvirtual task main_phase;\n @(posedge clk);\nendtask\nendclass\n'
            path.write_text(before)
            with patch.object(model,'ROOT',root),patch.object(model,'STATE',root/'state'),patch.object(model.eda,'HERE',root/'simulation/vcs'):
                case=model.case('test_read')
                model.save_case(dict(name='test_read',version=case['version'],phase='virtual task main_phase;\n @(posedge clk); #1;\nendtask'))
                self.assertIn('num_cycles=91',path.read_text())
                self.assertIn('#1;',path.read_text())
                saved=path.read_bytes()
                with self.assertRaises(ValueError):model.save_case(dict(name='test_read',version=case['version'],phase=case['phase']))
                self.assertEqual(path.read_bytes(),saved)

    def test_plan_exact_per_case_counts_and_seed_ranges(self):
        plan=dict(version=1,cases=[dict(case='tc_directed',runs=2,seed=41,cycles=64),dict(case='tc_random',runs=3,seed=7,cycles=2000),dict(case='tc_reset',enabled=False,runs=999,seed=1,cycles=64)])
        self.assertEqual(regression_plan.expand(plan),[('tc_directed',41,64,''),('tc_directed',42,64,''),('tc_random',7,2000,''),('tc_random',8,2000,''),('tc_random',9,2000,'')])
        plan['cases'][1]['case']='tc_directed'
        with self.assertRaises(ValueError):regression_plan.expand(plan)

    def test_plan_invalid_counts_seeds_and_fractional_values(self):
        for field,value in [('runs',0),('runs',10001),('seed',2147483647),('cycles',2.5),('cycles',True)]:
            row=dict(case='tc_random',runs=2,seed=1,cycles=64);row[field]=value
            with self.assertRaises(ValueError):regression_plan.expand(dict(version=1,cases=[row]))

    def test_dut_files_and_four_state_interface_unchanged(self):
        cfg=dut_config.load()
        self.assertTrue(all(dut_config.project_path(p).is_file() for p in cfg['files']))
        with self.assertRaises(ValueError):dut_config.project_path('../../outside.sv')
        wrong=dict(cfg,instance='module other; endmodule')
        with self.assertRaises(ValueError):dut_config.validate(wrong)
        self.assertIn(('rd_mem_status','input','[1:0] [7:0]'),dut_config.PORTS)

    def test_vcd_actual_edges_four_state_aliases_and_window_initial_value(self):
        source='''$timescale 1 ps $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 4 # data [3:0] $end
$scope module dut $end
$var wire 1 ! clk_alias $end
$upscope $end
$upscope $end
$enddefinitions $end
$dumpvars
0!
bx #
$end
#5000
1!
b1010 #
#7000
bz #
#10000
0!
'''
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'waves.vcd';path.write_text(source)
            db=vcd.index(path,root/'index');meta=vcd.describe(db)
            self.assertEqual(meta['timescale'],'1 ps');self.assertEqual(meta['end'],10000)
            ids={s['name']:s['id'] for s in meta['signals']}
            result=vcd.window(db,[ids['tb.clk'],ids['tb.dut.clk_alias'],ids['tb.data[3:0]']],6000,10000)
            self.assertEqual(result['signals'][0]['values'],[(6000,'1'),(10000,'0')])
            self.assertEqual(result['signals'][0]['values'],result['signals'][1]['values'])
            self.assertEqual(result['signals'][2]['values'],[(6000,'1010'),(7000,'z')])
            self.assertEqual(db,vcd.index(path,root/'index'))


if __name__=='__main__':unittest.main()
