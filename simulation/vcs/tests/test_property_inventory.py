"""Browse assertions and assumptions without changing the Cover search inventory."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eda
import functional_properties as props
import property_inventory
import rtl_cover


def property_node(kind, line, label='', immediate=False, file='device.sv'):
    node = dict(kind='ImmediateAssertion' if immediate else 'ConcurrentAssertion',
                assertionKind=kind, source_file_start=file, source_line_start=line,
                source_line_end=line, source_column_start=1)
    return dict(kind='Block', block='10 ' + label, body=node) if label else node


class PropertyInventoryTests(unittest.TestCase):
    def test_active_dut_discovers_all_three_types_from_the_same_instance(self):
        members = [property_node('Assert', 2, 'A_LIMIT'), property_node('Assume', 3, 'M_ENV'),
                   property_node('CoverProperty', 4, 'C_GOAL'), property_node('Assert', 5, immediate=True)]
        disabled = dict(kind='GenerateBlock', name='disabled', isUninstantiated=True,
                        members=[property_node('Assert', 5, 'A_DISABLED')])
        ast = {'design': {'members': [dict(kind='Instance', name=props.TOP, members=[
            dict(kind='Instance', name='dut', members=members + [disabled]),
            dict(kind='Instance', name='unused', members=[property_node('Assert', 2, 'A_UNUSED')])])]}}
        source = (b'module device;\nA_LIMIT: assert property (limit);\nM_ENV: assume property (enable);\n'
                  b'C_GOAL: cover property (ready);\nalways @(posedge clk) assert (valid);\nendmodule\n')
        rows = rtl_cover.collect(ast, Path('.'), {'device.sv': source})
        self.assertEqual([row['kind'] for row in rows], ['assert', 'assume', 'cover', 'assert'])
        self.assertEqual([row['hierarchy'] for row in rows[:3]], ['dut.A_LIMIT', 'dut.M_ENV', 'dut.C_GOAL'])
        self.assertEqual(rows[-1]['code'], 'always @(posedge clk) assert (valid);')
        self.assertFalse(rtl_cover.has_covers({'device.sv': b'A: assert (x); M: assume(y);'}))
        self.assertEqual(rtl_cover.native_hits('', rows[:2], True), set())

    def test_cover_client_expands_keyword_aliases_on_cold_and_warm_cache(self):
        rows = [dict(name='A', kind='assert'), dict(name='M', kind='assume'), dict(name='C', kind='cover')]
        dut = dict(defines=['CV=cover'], include_dirs=[], instance='d dut ();')
        sources = {'device.sv': b'A: assert (x); C: `CV property (x);'}
        self.assertFalse(rtl_cover.has_covers(sources))
        with tempfile.TemporaryDirectory() as folder, patch.object(eda, 'OUT', Path(folder)), \
                patch.object(rtl_cover, 'native_rows', return_value=rows) as scan:
            self.assertEqual(rtl_cover.latest_inventory(dut, sources), rows[-1:])
            self.assertEqual(rtl_cover.latest_inventory(dut, sources, covers_only=False), rows)
            self.assertEqual(rtl_cover.latest_inventory(dut, sources), rows[-1:])
            scan.assert_called_once()

    def test_cover_client_can_skip_elaboration_without_cover_or_macro_expansion(self):
        sources = {'device.sv': b'`default_nettype none\n`ifndef SYNTHESIS\nA: assert (x);\n`endif\n'
                                b'// `CV property (x)\ninitial $display("cover (x)");\n'}
        with patch.object(rtl_cover, 'native_rows') as scan:
            self.assertEqual(rtl_cover.latest_inventory({}, sources), [])
            scan.assert_not_called()

    def test_scan_failure_leaves_saved_checker_declarations_visible(self):
        source = b'// A_SKIP: assert property (never);\nA_KEEP: assert property (x);\nM_KEEP: assume (y);\n'
        dut = dict(defines=[], include_dirs=[])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'checks.sv').write_bytes(source)
            project = dict(checker='checks.sv', checker_includes=[])
            with patch.object(rtl_cover, 'load_current', return_value=(dut, {'device.sv': source})), \
                    patch.object(rtl_cover, 'latest_inventory', side_effect=ValueError('DUT error')), \
                    patch.object(property_inventory.toolchain, 'environment', side_effect=RuntimeError('unavailable')):
                report = property_inventory.inventory(root, project)
        self.assertEqual(set(report['issues']), {'rtl', 'checker'})
        self.assertEqual([row['label'] for row in report['properties']], ['A_KEEP', 'M_KEEP'] * 2)
        self.assertTrue(all(row['source_only'] for row in report['properties']))


if __name__ == '__main__':
    unittest.main()
