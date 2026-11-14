"""Inventory, exact DUT instance hits, and archived joint coverage."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contracts
import functional_cover
import functional_properties as properties
import rtl_cover


SOURCE = 'module d;\nC_REQ: cover property (@(posedge clk) disable iff (!reset) req ##2 done);\nendmodule\n'


def occurrence(kind, label='', line=2, column=1):
    return dict(kind='Block', block='123 '+label, body=dict(kind=kind, assertionKind='CoverProperty',
                source_file_start='from_dut.sv', source_line_start=line, source_line_end=line,
                source_column_start=column, source_column_end=69))


def dut_ast(*body):
    return {'design': {'members': [dict(kind='Instance', name=properties.TOP, body=dict(kind='InstanceBody',
        members=[dict(kind='Instance', name='dut', body=dict(kind='InstanceBody', members=list(body)))]))]}}


class RtlCoverTests(unittest.TestCase):
    def test_cover_inventory_tracks_nonzero_generate_instances(self):
        loop = dict(kind='GenerateBlockArray', name='bank', loopVariable='11 i', members=[
            dict(kind='GenerateBlock', branchKind='LoopIteration', constructIndex=i, name='', members=[
                dict(kind='Parameter', name='i', value=str(index)), occurrence('ConcurrentAssertion', 'C_REQ')])
            for i, index in enumerate((4, 9))])
        unused = dict(kind='GenerateBlock', name='unused', isUninstantiated=True,
                      members=[occurrence('ConcurrentAssertion', 'C_REQ')])
        rows = rtl_cover.collect(dut_ast(loop, unused), Path('.'), {'from_dut.sv': SOURCE.encode()})
        self.assertEqual([p['hierarchy'] for p in rows], ['dut.bank[4].C_REQ', 'dut.bank[9].C_REQ'])
        self.assertEqual(len({p['name'] for p in rows}), 2)
        self.assertEqual([p['code'].strip() for p in rows], [SOURCE.splitlines()[1]] * 2)
        self.assertEqual(list(rtl_cover.resolve_targets(rows, ('dut.bank[9].C_REQ',))), [rows[1]['name']])

    def test_original_sequence_is_preserved_for_vcs_and_lowered_for_formal(self):
        sources = {'from_dut.sv': SOURCE.encode()}
        formal = rtl_cover.formal_rewrites(sources)['from_dut.sv'].decode()
        self.assertIn('$past((req), 2)', formal)
        self.assertIn('if (!reset)', formal)
        self.assertEqual(len(formal.splitlines()), len(SOURCE.splitlines()))
        self.assertIn('req ##2 done', sources['from_dut.sv'].decode())

    def test_finite_range_guards_early_cycles_for_each_delay(self):
        predicate, history = rtl_cover.lower_cover('req ##[1:2] done', 'history')
        self.assertEqual(history, 2)
        self.assertIn('(history[0] && $past((req), 1))', predicate)
        self.assertIn('(history[1] && $past((req), 2))', predicate)
        self.assertTrue(predicate.endswith('&& (done))'))
        with self.assertRaises(ValueError):
            rtl_cover.lower_cover('req ##[1:$] done')

    def test_native_hits_are_keyed_by_instance_and_require_complete_log(self):
        leaf = [dict(kind='Instance', name=label, members=[occurrence('ConcurrentAssertion', 'C_REQ')])
                for label in ('left', 'right')]
        rows = rtl_cover.collect(dut_ast(*leaf), Path('.'), {'from_dut.sv': SOURCE.encode()})
        line = 'FUNCTIONAL_RTL_COVER\ttb_cmdbuf_rd.dut.{}.C_REQ\tfrom_dut.sv\t2\t{}\n'
        log = line.format('left', 2) + line.format('right', 0)
        manifest = properties.manifest(b'', rows)
        with self.assertRaisesRegex(ValueError, 'VCS'):
            properties.collect_hits(log, manifest, True)
        receipt = properties.collect_hits(log+'FUNCTIONAL_RTL_COMPLETE\nFUNCTIONAL_COMPLETE\n', manifest, True)
        self.assertEqual(receipt['hits'], [rows[0]['name']])
        self.assertEqual(properties.summary(manifest, [dict(status='PASS', functional=receipt)])['covered'], 1)

    def test_unsupported_target_is_reported_and_unselected_target_stays_unselected(self):
        rows = rtl_cover.collect(dut_ast(occurrence('ConcurrentAssertion','C_REQ')), Path('.'),
                                 {'from_dut.sv': SOURCE.encode()})
        manifest = properties.manifest(("C_IDLE: cover property (@(posedge clk) disable iff (!rst_n) !arvalid);\n")
                                       .encode(), rows)
        result = dict(functional=manifest, targets=[rows[0]['name']], unsupported={rows[0]['name']: 'reason'})
        functional_cover.finalize_cover(Path('.'), result, [], 'UNKNOWN')
        self.assertEqual(result['goals'], {'C_IDLE': 'NOT_SELECTED', rows[0]['name']: 'UNSUPPORTED'})
        self.assertEqual(result['status'], 'NO_WITNESSES')

    def test_contract_allows_passive_cover_but_still_rejects_changed_rtl(self):
        catalog = json.loads((contracts.HERE/'contracts.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in catalog['sources']:
                target = root/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((contracts.ROOT/relative).read_bytes())
            path = root/'rtl/cmdbuf_rd.sv'
            original = path.read_bytes()
            eol = b'\r\n' if b'\r\n' in original else b'\n'
            cover = (b'`ifndef SYNTHESIS'+eol+b'  C_NEW: cover property (@(posedge clk) arvalid);'+eol+b'`endif'+eol)
            path.write_bytes(original.replace(b'endmodule', cover+b'endmodule'))
            result = contracts.audit(root, copy.deepcopy(catalog), allow_rtl_covers=True)
            self.assertNotEqual(result['sources']['rtl/cmdbuf_rd.sv'], catalog['sources']['rtl/cmdbuf_rd.sv'])
            path.write_bytes(path.read_bytes().replace(b'assign rdata = (|rvalid)', b'assign rdata = (!rvalid)'))
            with self.assertRaisesRegex(ValueError, 'CONTRACT_UNSUPPORTED'):
                contracts.audit(root, catalog, allow_rtl_covers=True)


if __name__ == '__main__':
    unittest.main()
