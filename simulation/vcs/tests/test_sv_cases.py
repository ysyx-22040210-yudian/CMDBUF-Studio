import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import sv_cases


class SvAuthoringTests(unittest.TestCase):
    def test_copy_renames_class_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(sv_cases,'TC_DIR',Path(directory)):
            source=sv_cases.HERE/'tc'/'tc_directed.sv'
            created=sv_cases.new_case('my_read',source)
            cfg=sv_cases.load(created)
            self.assertEqual(cfg['name'],'my_read')
            self.assertIn('class my_read extends cmdbuf_main_tc;',cfg['source'])
            with self.assertRaises(FileExistsError):sv_cases.new_case('my_read',source)

    def test_archived_source_hash_and_override(self):
        cfg=sv_cases.load(sv_cases.TC_DIR/'tc_random.sv')
        self.assertEqual(sv_cases.program(cfg,42,2000),'MAIN_PHASE_V1 tc_random 42 2000\n')
        self.assertEqual(sv_cases.program(cfg,42),'MAIN_PHASE_V1 tc_random 42 0\n')
        for cycles in [0,-1,1000001]:
            with self.assertRaises(ValueError):sv_cases.program(cfg,42,cycles)
        with tempfile.TemporaryDirectory() as directory:
            entries=sv_cases.generate_factory(directory)
            archived=sv_cases.load(Path(directory)/'tc_sources'/'tc_random.sv')
            self.assertEqual(cfg['source_sha256'],archived['source_sha256'])
            self.assertEqual(len(entries),len({x['name'] for x in entries}))

    def test_filename_and_class_must_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'wrong_name.sv';p.write_text('class some_other_name extends cmdbuf_main_tc; endclass')
            with self.assertRaises(ValueError):sv_cases.load(p)

    def test_compile_copy_migrates_only_old_template_waits(self):
        source = ('@(posedge clk);\n#1; // old NBA wait\nbegin_cycle(cycle);\n'
                  'drive_control(); #1; sample_context();\n'
                  '// Keep this comment: #1;\n#1; drive_inputs();\n')
        adapted = sv_cases.align_template_delays(source)
        self.assertEqual(source.count('\n'), adapted.count('\n'))
        self.assertIn('   // old NBA wait\nbegin_cycle(cycle);', adapted)
        self.assertIn('drive_control();     sample_context();', adapted)
        self.assertTrue(adapted.endswith('// Keep this comment: #1;\n#1; drive_inputs();\n'))
