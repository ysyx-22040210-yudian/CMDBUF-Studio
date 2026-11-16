"""Inventory, exact DUT instance hits, and archived joint coverage."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
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

    def test_two_unnamed_covers_from_one_macro_are_kept_in_the_inventory(self):
        source = b'module d;\n`COVER_PAIR(req)\nendmodule\n'
        occurrences = [occurrence('ConcurrentAssertion') for _ in range(2)]
        for item in occurrences:
            item['body']['source_column_end'] = 1
        ast = dut_ast(*occurrences)
        rows = rtl_cover.collect(ast, Path('.'), {'from_dut.sv': source})
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row['name'] for row in rows}), 2)
        self.assertEqual(len({row['hierarchy'] for row in rows}), 2)
        self.assertTrue(all(row['ambiguous'] and row['unsupported'] for row in rows))
        self.assertEqual([row['code'] for row in rows], ['`COVER_PAIR(req)'] * 2)
        self.assertEqual(rtl_cover.collect(ast, Path('.'), {'from_dut.sv': source}), rows)
        self.assertEqual(rtl_cover.native_hits('FUNCTIONAL_RTL_COMPLETE\n', rows, True), set())

    def test_macro_arguments_are_expanded_only_in_the_formal_copy(self):
        call = '`DUT_COVER(C_SEQ,\n  req ##2 done)'
        prefix = '/* 跨拍 */ '
        source = ('module d;\n' + prefix + call + '\nendmodule\n').encode()
        header = b'`define DUT_COVER(NAME, EXPR) NAME: cover property (@(posedge clk) EXPR);\n'
        sources = {'from_dut.sv': source, 'macros.svh': header}
        node = occurrence('ConcurrentAssertion', 'C_SEQ', column=len(prefix.encode()) + 1)
        node['body']['source_column_end'] = node['body']['source_column_start']
        rows = rtl_cover.collect(dut_ast(node), Path('.'), sources)
        self.assertEqual(rows[0]['end_line'], 3)
        self.assertIn(call, rows[0]['code'])
        dut = dict(files=['from_dut.sv'], include_dirs=['.'], defines=['ENABLE_COVER'])
        expanded = 'C_SEQ: cover property (@(posedge clk) req ##2 done);'

        def preprocess(command, folder, log, env):
            self.assertEqual(command[:3], ['slang', '-E', '--comments'])
            self.assertIn('ENABLE_COVER', command)
            self.assertEqual((folder/'macros.svh').read_bytes(), header)
            marked = (folder/'from_dut.sv').read_text(encoding='utf-8')
            log.write_text(marked.replace(call, expanded), encoding='utf-8')
            return 0

        overlay = rtl_cover.expand_formal_macros(dut, sources, rows, {}, preprocess)
        self.assertIn(expanded.encode(), overlay['from_dut.sv'])
        self.assertEqual(overlay['from_dut.sv'].count(b'\n'), source.count(b'\n'))
        self.assertIn(b'$past((req), 2)', rtl_cover.formal_rewrites(overlay)['from_dut.sv'])
        self.assertEqual(sources['from_dut.sv'], source)
        self.assertIsNone(rtl_cover.cover_fragment(expanded + ' assign w = req;'))

        def preprocess_with_directive(command, folder, log, env):
            status = preprocess(command, folder, log, env)
            if '--directives' in command:
                text = log.read_text(encoding='utf-8').replace(expanded, expanded + '\n`define KEEP_BRANCH 1\n')
                log.write_text(text, encoding='utf-8')
            return status

        self.assertEqual(rtl_cover.expand_formal_macros(dut, sources, rows, {}, preprocess_with_directive), sources)

    def test_frontend_rejection_preserves_the_other_cover_on_the_same_line(self):
        file = 'from_dut.sv'
        text = 'C_A: cover property (@(posedge clk) a); C_B: cover property (@(posedge clk) b);'
        column = text.index(' b)') + 2
        diagnostic = 'rtl_formal/{}:1:{}: error: encountered unsupported SVA feature'.format(file, column)
        rows = [dict(file=file, line=1, label=label) for label in ('C_A', 'C_B')]
        sources = {file: text.encode()}
        rejected = rtl_cover.unsupported_declarations(diagnostic, sources, rows)
        self.assertEqual(list(rejected.values()), [(file, 1, 'C_B')])
        rewritten = rtl_cover.formal_rewrites(sources, rejected)[file].decode()
        self.assertIn('C_A: cover property (@(posedge clk) a);', rewritten)
        self.assertIn("C_B: cover property (@(posedge clk) 1'b0);", rewritten)

    def test_original_sequence_is_preserved_for_vcs_and_lowered_for_formal(self):
        sources = {'from_dut.sv': SOURCE.encode()}
        formal = rtl_cover.formal_rewrites(sources)['from_dut.sv'].decode()
        self.assertIn('$past((req), 2)', formal)
        self.assertRegex(formal, r'always @\(posedge clk or posedge test_mode_cover_history_\w+_cancel\)')
        self.assertEqual(len(formal.splitlines()), len(SOURCE.splitlines()))
        self.assertIn('req ##2 done', sources['from_dut.sv'].decode())

    def test_finite_range_guards_early_cycles_for_each_delay(self):
        predicate, history = rtl_cover.lower_cover('req ##[1:2] done', 'history')
        self.assertEqual(history, 2)
        self.assertIn('(history[0] && $past((req), 1))', predicate)
        self.assertIn('(history[1] && $past((req), 2))', predicate)
        self.assertTrue(predicate.endswith('&& (done))'))

    def test_editor_retains_original_cover_sequence_and_action_for_vcs(self):
        original = ('C_RANGE: cover sequence (@(posedge clk) disable iff (!rst_n) req ##[1:3] done) '
                    'begin $display("hit"); end\n')
        rows = properties.user_properties(original)
        self.assertEqual(rows[0]['form'], 'sequence')
        self.assertIn('$past', rows[0]['predicate'])
        self.assertIn('cover sequence', properties.simulation(rows))
        self.assertIn('$display("hit");', properties.simulation(rows))

    def test_legacy_vcs_uses_named_sequence_with_original_clock_reset_and_action(self):
        raw = (b'C_MULTIPLE: cover sequence (@(negedge clk) disable iff (!rst_n)\n'
               b'    req ##[1:3] ack) begin $display("match"); end\n')
        sources = {'d.sv': raw}
        result = rtl_cover.vcs_cover_sequences(sources)['d.sv']
        self.assertEqual(result.count(b'\n'), raw.count(b'\n'))
        self.assertRegex(result, rb'sequence (test_mode_vcs_sequence_\w+); req ##\[1:3\] ack; endsequence '
                                 rb'C_MULTIPLE: cover property \(@\(negedge clk\) disable iff \(!rst_n\) \1')
        self.assertIn(b'begin $display("match"); end', result)
        self.assertEqual(sources['d.sv'], raw)

    def test_editor_parses_clocked_immediate_assertion_and_cover(self):
        original = ('always @(posedge clk) begin\n'
                    'if (rst_n) begin C_I: cover (req) $display("hit"); end end\n'
                    'always @(posedge clk) if (rst_n) A_I: assert (ack) else $error("miss");\n')
        cover, assertion = properties.user_properties(original)
        self.assertEqual((cover['form'], assertion['form']), ('immediate', 'immediate'))
        native = properties.simulation([cover, assertion])
        self.assertIn('always @(posedge clk) if (rst_n) begin\nFC_C_C_I: cover (req)', native)
        self.assertIn('FC_USER_A_I: assert (ack) else begin\n$error("miss");', native)

    def test_formal_expands_module_level_named_properties_and_default_clock(self):
        source = (b'module d;\n'
                  b'default clocking cb @(posedge clk); endclocking\n'
                  b'sequence s; req ##1 done; endsequence\n'
                  b'C_NAMED: cover property (s);\n'
                  b'endmodule\n')
        issues = {}
        overlay = rtl_cover.formal_rewrites({'d.sv': source}, issues=issues)['d.sv']
        self.assertFalse(issues)
        self.assertEqual(source.count(b'\n'), overlay.count(b'\n'))
        self.assertIn(b'C_NAMED: cover property (@(posedge clk)', overlay)
        self.assertIn(b'$past((req), 1)', overlay)
        self.assertNotIn(b'endsequence', overlay)

    def test_assertion_abort_is_formal_only_and_cancels_history(self):
        source = ('A_ABORT: assert property (@(posedge clk) disable iff (!rst_n) '
                  'accept_on (cancel) req |=> done);\n')
        row = properties.user_properties(source)[0]
        self.assertIn('(cancel)', row['formal_reset'])
        self.assertTrue(any('or posedge' in line for line in row['formal_helpers']))
        self.assertIn('accept_on (cancel)', properties.simulation([row]))
        formal = functional_cover.formal_top(dict(instance='d dut();'), [], [row], 'bmc')
        self.assertIn('disable iff ((!rst_n) || ((cancel)))', formal)

    def test_named_composition_and_clocked_sample_are_preserved_in_vcs(self):
        original = ('sequence req_pair; req[*2]; endsequence\n'
                    'property request_then_done; req_pair |=> done; endproperty\n'
                    'C_PAIR: cover property (@(posedge clk) disable iff (!rst_n) request_then_done);\n'
                    'C_SAMPLE: cover property (@(posedge clk) disable iff (!rst_n) '
                    '$past(signed_data, 2, gate, @(posedge clk)) < 0);\n')
        manifest = properties.manifest(original.encode())
        pair, sample = manifest['properties']
        self.assertEqual(pair['bounded_model'], 'cover')
        self.assertIn('req[*2]', pair['formal_expression'])
        self.assertIn('typedef type(signed_data)', ' '.join(sample['formal_helpers']))
        native = properties.simulation(manifest['properties'], manifest['declarations'])
        self.assertIn('property request_then_done; req_pair |=> done; endproperty', native)
        self.assertIn('(request_then_done)', native)
        self.assertIn('@(posedge clk)) < 0', native)

    def test_repeat_expansion_at_normal_solver_depth(self):
        for expression in ('done[->2] ##1 tail', 'done[=1:2] ##1 tail'):
            with self.subTest(expression=expression):
                _, statements = properties.structured_cover(expression, 'seq', 'clk', '!rst_n', 50)
                self.assertLess(sum(map(len, statements)), 50000)

    @unittest.skipUnless(shutil.which('iverilog') and shutil.which('vvp'), 'requires Icarus Verilog')
    def test_first_match_and_nonconsecutive_repeats_end_at_the_required_sample(self):
        first, first_helpers = properties.structured_cover(
            'first_match(req ##[1:4] done) ##1 tail', 'first', 'clk', '!rst_n', 9)
        goto, goto_helpers = properties.structured_cover(
            'done[->2] ##1 tail', 'goto', 'clk', '!rst_n', 9)
        nonconsecutive, nonconsecutive_helpers = properties.structured_cover(
            'done[=2] ##1 tail', 'nonconsecutive', 'clk', '!rst_n', 9)
        source = '''
module sequence_probe;
    reg clk=0, rst_n=1, req=0, done=0, tail=0;
    %s
    wire first_match_hit = %s, goto_hit = %s, nonconsecutive_hit = %s;
    task step(input a,b,c,first_expected,goto_expected,nonconsecutive_expected);
        req=a; done=b; tail=c; #2;
        if (first_match_hit !== first_expected || goto_hit !== goto_expected ||
            nonconsecutive_hit !== nonconsecutive_expected)
            $fatal(1, "unexpected sequence match");
        clk=1; #2; clk=0;
    endtask
    initial begin
        step(1,0,0,0,0,0);
        step(0,1,0,0,0,0);
        step(0,0,1,1,0,0);
        step(0,1,0,0,0,0);
        step(0,0,1,0,1,1);
        step(0,0,1,0,0,1);
        $finish;
    end
endmodule
''' % ('\n    '.join(first_helpers + goto_helpers + nonconsecutive_helpers), first, goto, nonconsecutive)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path/'probe.sv').write_text(source, encoding='utf-8')
            for command in (['iverilog', '-g2012', '-s', 'sequence_probe', '-o', 'probe.vvp', 'probe.sv'],
                            ['vvp', 'probe.vvp']):
                run = subprocess.run(command, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     encoding='utf-8', errors='replace', timeout=20)
                self.assertEqual(run.returncode, 0, run.stdout)

    def test_unbounded_cover_is_available_in_the_dut_and_the_property_editor(self):
        source = SOURCE.replace('req ##2 done', 'req ##[1:$] done')
        sources = {'from_dut.sv': source.encode()}
        rows = rtl_cover.collect(dut_ast(occurrence('ConcurrentAssertion', 'C_REQ')), Path('.'), sources)
        self.assertNotIn('unsupported', rows[0])
        issues = {}
        compiled = rtl_cover.formal_rewrites(sources, issues=issues)['from_dut.sv'].decode()
        self.assertFalse(issues)
        self.assertNotIn('##', compiled)
        self.assertIn('C_REQ: cover property (@(posedge clk) disable iff (!reset)', compiled)
        self.assertEqual(compiled.count('\n'), source.count('\n'))

        authored = properties.user_properties(source.splitlines()[1].replace('!reset', '!rst_n'))
        formal = functional_cover.formal_top(dict(instance='d dut();'), [], authored, 'cover')
        self.assertIn("reg fc_wait_C_REQ_0_seen = 1'b0;", formal)
        self.assertIn('FC_C_C_REQ: cover property', formal)
        self.assertIn('req ##[1:$] done', properties.simulation(authored))

    def test_error_in_generated_wait_state_is_attributed_to_its_cover(self):
        name = 'from_dut.sv'
        source = ('C_BAD: cover property (@(posedge clk) $onehot(req) ##[1:$] done); '
                  'C_GOOD: cover property (@(posedge clk) req);')
        lowered = rtl_cover.formal_rewrites({name: source.encode()})
        lowered_text = lowered[name].decode()
        column = lowered_text.index('$onehot(req)') + 1
        diagnostic = "rtl_formal/{}:1:{}: error: unsupported system task '$onehot'".format(name, column)
        rows = [dict(file=name, line=1, label=label) for label in ('C_BAD', 'C_GOOD')]
        rejected = rtl_cover.unsupported_declarations(diagnostic, lowered, rows)
        self.assertEqual(list(rejected.values()), [(name, 1, 'C_BAD')])
        fallback = rtl_cover.formal_rewrites(lowered, rejected)[name].decode()
        self.assertNotIn('$onehot(req)', fallback)
        self.assertIn('C_GOOD: cover property (@(posedge clk) req);', fallback)

    @unittest.skipUnless(shutil.which('iverilog') and shutil.which('vvp'), 'requires Icarus Verilog')
    def test_wait_state_has_no_upper_bound_and_cancels_between_clock_edges(self):
        predicate, state = properties.unbounded_cover('req ##[1:$] done', 'wait_test')
        bench = '''
module delay_probe;
    reg clk = 0, rst_n = 1, req = 0, done = 0;
    %s
    wire hit = %s;

    task sample(input r, d, expected);
        req = r;
        done = d;
        #3;
        if (hit !== expected)
            $fatal(1, "at %%0t, hit=%%b expected=%%b", $time, hit, expected);
        clk = 1;
        #2 clk = 0;
    endtask

    initial begin
        sample(1, 1, 0);
        sample(0, 1, 1);
        repeat (40) sample(0, 0, 0);
        sample(0, 1, 1);
        #1 rst_n = 0;
        #1 rst_n = 1;
        sample(0, 1, 0);
        sample(1, 0, 0);
        sample(0, 1, 1);
        $finish;
    end
endmodule
''' % ('\n    '.join(state), predicate)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'probe.sv'
            binary = Path(folder)/'probe.vvp'
            source.write_text(bench, encoding='utf-8')
            for command in (['iverilog', '-g2012', '-s', 'delay_probe', '-o', str(binary), str(source)],
                            ['vvp', str(binary)]):
                run = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     encoding='utf-8', errors='replace', timeout=20)
                self.assertEqual(run.returncode, 0, run.stdout)

    def test_native_hits_are_keyed_by_instance_and_require_complete_log(self):
        leaf = [dict(kind='Instance', name=label, members=[occurrence('ConcurrentAssertion', 'C_REQ')])
                for label in ('left', 'right')]
        rows = rtl_cover.collect(dut_ast(*leaf), Path('.'), {'from_dut.sv': SOURCE.encode()})
        line = 'FUNCTIONAL_RTL_COVER\ttb_test_mode_rd.dut.{}.C_REQ\tfrom_dut.sv\t2\t{}\n'
        log = line.format('left', 2) + line.format('right', 0)
        manifest = properties.manifest(b'', rows)
        with self.assertRaisesRegex(ValueError, 'VCS'):
            properties.collect_hits(log, manifest, True)
        receipt = properties.collect_hits(log+'FUNCTIONAL_RTL_COMPLETE\nFUNCTIONAL_COMPLETE\n', manifest, True)
        self.assertEqual(receipt['hits'], [rows[0]['name']])
        self.assertEqual(properties.summary(manifest, [dict(status='PASS', functional=receipt)])['covered'], 1)
        unreadable = line.format('left', 2) + line.format('right', -1)
        receipt = properties.collect_hits(unreadable+'FUNCTIONAL_RTL_COMPLETE\nFUNCTIONAL_COMPLETE\n', manifest, True)
        self.assertEqual(receipt['hits'], [rows[0]['name']])
        self.assertEqual(receipt['vcs_unavailable'], [rows[1]['name']])

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
            path = root/'rtl/test_mode_rd.sv'
            original = path.read_bytes()
            eol = b'\r\n' if b'\r\n' in original else b'\n'
            cover = (b'`ifndef SYNTHESIS'+eol+b'  C_NEW: cover property (@(posedge clk) arvalid);'+eol+b'`endif'+eol)
            path.write_bytes(original.replace(b'endmodule', cover+b'endmodule'))
            result = contracts.audit(root, copy.deepcopy(catalog), allow_rtl_covers=True)
            self.assertNotEqual(result['sources']['rtl/test_mode_rd.sv'], catalog['sources']['rtl/test_mode_rd.sv'])
            path.write_bytes(path.read_bytes().replace(b'assign rdata = (|rvalid)', b'assign rdata = (!rvalid)'))
            with self.assertRaisesRegex(ValueError, 'CONTRACT_UNSUPPORTED'):
                contracts.audit(root, catalog, allow_rtl_covers=True)


if __name__ == '__main__':
    unittest.main()
