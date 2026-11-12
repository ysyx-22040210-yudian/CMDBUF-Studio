from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gui'))
from coverage_data import CoverageData, COVERED, EXCLUDED, MISSED, PARTIAL, line_coverage, urg_scores


class CoverageBrowserTests(unittest.TestCase):
    def test_indented_urg_rows_keep_instance_scores(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'hierarchy.txt'
            path.write_text('SCORE LINE COND NAME\n 81.20 90.00 72.40 tb\n'
                            '  SCORE LINE COND\n   80.00 88.00 72.00 dut\n'
                            '    SCORE LINE COND\n    100.00 100.00 100.00 cmd\n'
                            '     60.00 70.00 50.00 otb\n', encoding='utf-8')
            scores = urg_scores(path, hierarchy=True)
            self.assertEqual(scores['tb.dut.cmd']['LINE'], '100.00')
            self.assertEqual(scores['tb.dut.otb']['COND'], '50.00')

    def test_scopes_and_line_badges_use_bins_without_counting_groups(self):
        def instance(name, parent, module):
            return dict(instance=name, parent_instance=parent, module=module)
        def item(id, name, line, hit, status=0, bin_id=None):
            return dict(id=id, instance=name, metric='COND', file='rtl.sv', line=line,
                        covered=hit, total=1, status=status, parent=-1, name=str(id),
                        bin=id if bin_id is None else bin_id)

        data = CoverageData([
            instance('tb', '', 'tb'),
            instance('tb.u1', 'tb', 'dut'),
            item(0, 'tb.u1', 14, 0, bin_id=-1),
            item(1, 'tb.u1', 14, 1),
            item(2, 'tb.u1', 14, 0),
            item(3, 'tb.u1', 19, 0, status=4),
            instance('tb.u2', 'tb', 'dut'),
            item(4, 'tb.u2', 14, 1),
            item(5, 'tb.u2', 23, 0),
        ])
        scope = list(data.scoped_items('实例层级', 'tb.u1', 'COND'))
        self.assertEqual(line_coverage(scope), {('rtl.sv', 14):(1, 2, PARTIAL), ('rtl.sv', 19):(0, 0, EXCLUDED)})
        module = list(data.scoped_items('模块', 'dut', 'COND'))
        self.assertEqual(line_coverage(module)[('rtl.sv', 23)], (0, 1, MISSED))
        self.assertEqual(line_coverage([module[-2]])[('rtl.sv', 14)], (1, 1, COVERED))
        self.assertEqual(data.missing['实例层级', 'tb', 'COND'], 2)
        self.assertEqual(data.missing['模块', 'dut', 'COND'], 2)
