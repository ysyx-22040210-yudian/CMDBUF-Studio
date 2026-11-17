"""Exercise commercial Cover import, replay receipts, and coverage admission."""
from contextlib import redirect_stdout
import csv
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'gui')]
import backend
import commercial_formal as commercial
import commercial_replay as replay
from code_coverage import engine as coverage


eda = commercial.eda
props = commercial.props
IDLE = dict({name: 0 for name, _ in props.INPUTS}, src=3)


class CommercialReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name).resolve()
        output = patch.object(eda, 'OUT', self.out)
        output.start()
        self.addCleanup(output.stop)
        self.job = self.out / 'commercial_formal' / 'site'
        self.job.mkdir(parents=True)
        self.request = commercial.build_request(self.job, 'cover', 48, 10, [], None, ['SYNTHESIS'])
        eda.write_json(self.job / 'request.json', self.request)
        self.request_hash = eda.digest(self.job / 'request.json')
        self.build = dict(dut=commercial.dut_config.load(), sources=eda.manifest(),
                          functional=props.manifest(props.USER_FILE.read_bytes()),
                          contract_revision=eda.contracts.REVISION)

    def cover(self, name='C_PARITY_R', **artifacts):
        return dict(name=commercial.TOP + '.' + name, label=name.split('.')[-1],
                    kind='cover', status='REACHED', depth=1, **artifacts)

    def save_result(self, properties):
        answer = dict(schema=commercial.SCHEMA, run_id='site', request_sha256=self.request_hash,
                      engine=dict(name='Fixture', version='1'), complete=True, properties=properties)
        result = commercial.normalize(self.job, self.request, self.request_hash, answer)
        result.update(id='site', mode='cover', request_sha256=self.request_hash)
        eda.write_json(self.job / 'result.json', result)
        return result

    def save_json(self, *frames):
        eda.write_json(self.job / 'inputs.json', dict(schema=replay.STIMULUS_SCHEMA, frames=list(frames)))
        return 'inputs.json'

    def test_historical_vcd_samples_before_the_rising_edge(self):
        lines = ['$timescale 1 ns $end', '$scope module {} $end'.format(commercial.TOP),
                 '$var wire 1 c clk $end', '$var wire 1 r rst_n $end']
        signals = {name: 'p{}'.format(i) for i, (name, _) in enumerate(props.INPUTS)}
        for name, width in props.INPUTS:
            reference = '\\' + name if '[' in name else name
            lines.append('$var wire {} {} {}{} $end'.format(
                width, signals[name], reference, ' [{}:0]'.format(width-1) if width > 1 else ''))
        lines += ['$upscope $end', '$enddefinitions $end', '#0', '$dumpvars', '0c', '0r']
        lines += ['b{:b} {}'.format(IDLE[name], code) for name, code in signals.items()]
        lines += ['$end', '#1', '1r', '#10', '1c', 'b101 ' + signals['araddr'], '#15', '0c', '#20', '1c']
        target = self.job / 'original.vcd'
        target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        receipt = self.save_result([self.cover(trace=target.name)])
        receipt['properties'][0].pop('trace_sha256')
        eda.write_json(self.job / 'result.json', receipt)

        prepared = replay.prepare('site')
        replay.check_build(prepared, self.build)
        _, case = next(iter(prepared['cases'].values()))
        self.assertEqual(case['goals'], {'C_PARITY_R': 1})
        self.assertEqual([int(value, 16) for value in case['frames']],
                         [props.packed(IDLE), props.packed(dict(IDLE, araddr=5))])

    def test_shared_inputs_resolve_same_labels_by_full_instance(self):
        stimulus = self.save_json(IDLE)
        self.save_result([self.cover('dut.u0.C_DUP', stimulus=stimulus),
                          self.cover('dut.u1.C_DUP', stimulus=stimulus)])
        for i in range(2):
            self.build['functional']['properties'].append(dict(
                kind='cover', label='C_DUP', hierarchy='dut.u{}.C_DUP'.format(i), name='RTL_{}'.format(i)))
        selected = replay.prepare('site', ['p1'])
        replay.check_build(selected, self.build)
        self.assertEqual(selected['cases']['cf_site_p1'][1]['goals'], {'RTL_1': 1})
        both = replay.prepare('site')
        replay.check_build(both, self.build)
        self.assertEqual(list(both['cases']), ['cf_site_p0'])
        self.assertEqual(both['cases']['cf_site_p0'][1]['goals'], {'RTL_0': 1, 'RTL_1': 1})
        self.build['dut']['defines'] = ['CHANGED']
        with self.assertRaisesRegex(ValueError, '版本不同'):
            replay.check_build(both, self.build)

    def test_inaccessible_or_incomplete_inputs_cannot_be_replayed(self):
        self.save_result([self.cover()])
        with self.assertRaisesRegex(ValueError, '没有带 VCD'):
            replay.prepare('site')
        stimulus = self.save_json(dict(IDLE, pc_err='x'))
        self.save_result([self.cover(stimulus=stimulus)])
        with self.assertRaisesRegex(ValueError, 'pc_err.*X/Z'):
            replay.prepare('site')
        self.save_json(IDLE)
        self.save_result([self.cover(stimulus=stimulus)])
        self.save_json(dict(IDLE, araddr=1))
        with self.assertRaisesRegex(ValueError, '轨迹已改变'):
            replay.prepare('site')

    def test_regression_and_joint_selection_use_simulated_hits(self):
        self.save_result([self.cover(stimulus=self.save_json(IDLE))])
        build_dir = self.out / 'builds' / 'fixture'
        (build_dir / 'simv.vdb').mkdir(parents=True)
        authored = build_dir / 'checker' / 'original' / props.USER_FILE.name
        authored.parent.mkdir(parents=True)
        authored.write_bytes(props.USER_FILE.read_bytes())
        eda.write_json(build_dir / 'build.json', self.build)
        report = dict(runtime={}, integrity_errors=[], failures=[],
                      environment=dict(completed=True, failures=[]))

        def sim(cmd, run, log, env, timeout, **options):
            if cmd[0] == 'fsdb2vcd':
                (run / 'waves.vcd').write_text('fixture VCD', encoding='utf-8')
                return 0, 0
            self.assertIn('+SV_TC', cmd)
            self.assertEqual((run / 'stimulus.txt').read_text(encoding='utf-8'),
                             'COMMERCIAL_COVER_V1 cf_site_p0 1 1\n{:x}\n'.format(props.packed(IDLE)))
            (run / 'waves.fsdb').write_bytes(b'fixture FSDB')
            eda.write_json(run / 'sim.process.json', {})
            self.simulated_samples(run)
            log.write_text(('FUNCTIONAL_HIT C_PARITY_R\n' if hit else '') +
                           'FUNCTIONAL_COMPLETE\nTEST_PASS fixture\n', encoding='utf-8')
            return 0, 0.01

        merged = dict(status='PASS', metrics_percent=dict(LINE='10', COND='10', TOGGLE='10', BRANCH='10'))
        args = eda.parser().parse_args(['formal-replay', '--commercial-run', 'site'])
        with patch.object(eda, 'build_workspace', return_value=(build_dir, self.build['sources'])), \
             patch.object(eda, 'execute', side_effect=sim), \
             patch.object(eda.check_policy, 'report', return_value=report), \
             patch.object(eda, 'merge_coverage_databases', return_value=merged), redirect_stdout(StringIO()):
            hit = False
            self.assertNotEqual(eda.regress(args, {}), 0)
            failed = eda.read_json(eda.select_regression('latest') / 'summary.json')
            self.assertEqual(failed['coverage']['status'], 'NO_PASSING_CASES')
            self.assertTrue(any('did not hit' in error for error in failed['results'][0]['failures']))
            hit = True
            self.assertEqual(eda.regress(args, {}), 0)

        batch = eda.select_regression('latest')
        summary = eda.read_json(batch / 'summary.json')
        self.assertEqual(backend.history()[0]['commercial_run'], 'site')
        self.assertEqual(summary['results'][0]['wave_artifacts'], ['waves.fsdb', 'waves.vcd'])

        ordinary = batch.parent / 'ordinary'
        ordinary_case = ordinary / 'cases' / 'tc_directed_seed_1'
        (ordinary_case / 'simv.vdb').mkdir(parents=True)
        original = summary['results'][0]
        hit_record = dict(original['functional'], hits=['C_XAU0_AR'])
        receipt = ordinary_case / 'functional_hits.json'
        eda.write_json(receipt, hit_record)
        normal = dict(original, id=ordinary_case.name, case='tc_directed', functional=hit_record,
                      functional_receipt_sha256=eda.digest(receipt))
        normal.pop('formal_origin')
        ordinary_summary = dict(summary, id=ordinary.name, results=[normal],
                                coverage=dict(summary['coverage'], included=[normal['id']]))
        ordinary_summary.pop('commercial_formal')

        cases = list(coverage.case_inputs(batch, summary, require_vdb=True))
        cases.extend(coverage.case_inputs(ordinary, ordinary_summary, require_vdb=True))
        _, hits, functional_masks = coverage.functional_coverage([batch, ordinary], [summary, ordinary_summary], cases)
        self.assertEqual(hits['C_PARITY_R'], 1)
        self.assertEqual(hits['C_XAU0_AR'], 1)
        joint = [(mask << 2) | (1 << index) for index, mask in enumerate(functional_masks)]
        self.assertEqual(coverage.minimum_cover(joint)['selected'], [0, 1])

    @staticmethod
    def simulated_samples(run):
        # One witness frame launches at reset release. The next eight frames
        # use the normal simulator-generated quiet tail.
        eda.write_json(run / 'tc_config.json', dict(name='cf_site_p0', seed=1, num_cycles=1,
                       bootstrap_edges=0, drive_offset_ps=0,
                       startup=dict(clock_initial=1, reset_release_ps=1000, first_posedge_ps=10000)))
        samples, assignments = [], []
        clock = ['time_ps,clk,rst_n', '0,1,0', '1000,1,1', '5000,0,1']
        for edge in range(9):
            tick = (edge + 1) * 10000
            launch = 1000 if not edge else tick - 10000
            phase = 2 if edge else 0
            pins = IDLE if not edge else dict(IDLE, rready=7, rw_rd_gnt=255)
            sample = {name: format(pins[name], '0{}b'.format(width)) for name, width in props.INPUTS}
            sample.update(cycle=edge, edge=edge, time_ps=tick, phase=phase, clk=1, rst_n=1,
                          **dict.fromkeys(('arready', 'req', 'arb_addr', 'rvalid', 'rdata', 'rid',
                                           'r_port_id', 'raddr', 'rresp', 'pending_cnt'), '0'))
            samples.append(sample)
            assignments.append(dict(sample, time_ps=launch, launch_ps=launch, expected_sample_ps=tick,
                                    clk_low_ns=5, clk_high_ns=5, clk_pause_ns=0, issue_data=0, issue_error=0))
            if edge:
                clock.append('{},0,1'.format(tick - 5000))
            clock.append('{},1,1'.format(tick))
        for name, rows in [('samples.csv', samples), ('assigned.csv', assignments)]:
            with (run / name).open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        (run / 'clock_reset.csv').write_text('\n'.join(clock) + '\n', encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
