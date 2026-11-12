import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_policy
import eda
from gui import model


class CheckPolicyTests(unittest.TestCase):
    def test_old_plan_is_not_silently_relaxed(self):
        self.assertEqual(check_policy.plan_options({}), dict(check_mode='strict', assert_fatal=True, env_fatal=True))
        self.assertEqual(model.default_plan()['check_mode'], 'transaction')
        self.assertEqual(check_policy.plan_options(dict(check_mode='transaction',assert_fatal=False,env_fatal=True)),
                         dict(check_mode='transaction',assert_fatal=False,env_fatal=True))
        for options in [dict(check_mode='auto'), dict(assert_fatal='false'), dict(assert_fatal=0), dict(env_fatal='false'), dict(env_fatal=0)]:
            with self.assertRaises(ValueError): check_policy.plan_options(options)

    def test_nonfatal_failure_is_failed_and_scope_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'check_runtime.json').write_text(json.dumps(dict(mode='transaction',assert_fatal=0,
                failures=1,incomplete=0,finished=1,sampled_frames=8,checked_frames=8)))
            (root/'assertions.jsonl').write_text(json.dumps(dict(label='T_R_ID_ORDER',edge=8))+'\n')
            report=check_policy.report(root,dict(check_mode='transaction',assert_fatal=False))
            self.assertFalse(report['integrity_errors'])
            self.assertEqual([g['status'] for g in report['groups']],['PASS','FAIL','NOT_CHECKED'])
            self.assertEqual(len(report['failures']),1)

    def test_final_frame_and_failure_archive_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'check_runtime.json').write_text(json.dumps(dict(mode='strict',assert_fatal=1,
                failures=2,incomplete=0,finished=1,sampled_frames=8,checked_frames=7)))
            report=check_policy.report(root,dict(check_mode='strict',assert_fatal=True))
            self.assertIn('unconsumed snapshot at end of simulation',report['integrity_errors'])
            self.assertIn('assertion count does not match archived failures',report['integrity_errors'])
            self.assertTrue(all(g['status']=='INCOMPLETE' for g in report['groups']))

    def test_unfinished_transactions_are_not_interface_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'check_runtime.json').write_text(json.dumps(dict(mode='transaction',assert_fatal=1,
                failures=0,incomplete=1,finished=1,sampled_frames=8,checked_frames=8)))
            report=check_policy.report(root,dict(check_mode='transaction',assert_fatal=True))
            self.assertEqual([g['status'] for g in report['groups']],['PASS','INCOMPLETE','NOT_CHECKED'])

    def test_common_constraints_stay_active_in_generic_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            eda.instrument_checker(Path(tmp))
            data=eda.read_json(Path(tmp)/'instrumentation.json')
            props={p['label']:p for f in data for p in f['properties']}
            for label in check_policy.COMMON:
                self.assertEqual(props[label]['scope'],'common')
            for label in ('A_COMPARE_PENDING_CNT','A_COMPARE_REQ','A_COMPARE_RVALID'):
                self.assertEqual(props[label]['scope'],'strict')

    def test_interrupted_archive_is_a_reported_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'check_runtime.json').write_text('{"mode":')
            (root/'assertions.jsonl').write_text('{"label":"T_R_ID_ORDER"}\n{"label":')
            report=check_policy.report(root,dict(check_mode='transaction',assert_fatal=False))
            self.assertEqual(len(report['failures']),1)
            self.assertTrue(any('invalid check_runtime' in x for x in report['integrity_errors']))
            self.assertTrue(any('invalid assertions.jsonl line 2' in x for x in report['integrity_errors']))
            self.assertEqual(report['groups'][1]['status'],'FAIL')

    def test_environment_master_preserves_requested_assertion_setting(self):
        plan=dict(check_mode='transaction', assert_fatal=True, env_fatal=False)
        self.assertEqual(check_policy.plan_options(plan), plan)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'check_runtime.json').write_text(json.dumps(dict(mode='transaction', assert_fatal=1,
                env_fatal=0, effective_assert_fatal=0, failures=0, incomplete=0, finished=1,
                sampled_frames=16, checked_frames=16)))
            (root/'environment_runtime.json').write_text(json.dumps(dict(env_fatal=0,
                failures=2, unrecoverable=0, finished=1)))
            records=[dict(label=label, unrecoverable=0) for label in ('MAIN_T_MEM4', 'MAIN_PIN_MODIFICATION')]
            (root/'environment_errors.jsonl').write_text('\n'.join(json.dumps(r) for r in records)+'\n')
            report=check_policy.report(root, plan, environment_required=True)
            self.assertFalse(report['integrity_errors'])
            self.assertTrue(report['assert_fatal'])
            self.assertFalse(report['effective_assert_fatal'])
            self.assertTrue(report['environment']['completed'])
            self.assertEqual(report['environment']['status'], 'FAIL')
            self.assertEqual(len(report['environment']['failures']), 2)
            self.assertNotIn('PASS', [g['status'] for g in report['groups']])

    def test_environment_archive_and_effective_policy_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            plan=check_policy.normalize(env_fatal=False)
            (root/'check_runtime.json').write_text(json.dumps(dict(mode='transaction', assert_fatal=1,
                env_fatal=1, effective_assert_fatal=1, failures=0, incomplete=0, finished=1,
                sampled_frames=16, checked_frames=16)))
            (root/'environment_runtime.json').write_text(json.dumps(dict(env_fatal=0,
                failures=2, unrecoverable=1, finished=1)))
            (root/'environment_errors.jsonl').write_text('{"label":"MAIN_T_MEM4","unrecoverable":0}\n')
            report=check_policy.report(root, plan, environment_required=True)
            self.assertIn('environment error count does not match archive', report['integrity_errors'])
            self.assertIn('unrecoverable environment error count does not match archive', report['integrity_errors'])
            self.assertIn('runtime effective assertion fatal policy differs from requested policy', report['integrity_errors'])
            (root/'environment_errors.jsonl').write_text('{"label":')
            broken=check_policy.report(root, plan, environment_required=True)
            self.assertTrue(any('invalid environment_errors.jsonl' in e for e in broken['integrity_errors']))

    def test_old_binary_report_does_not_require_new_environment_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'check_runtime.json').write_text(json.dumps(dict(mode='strict', assert_fatal=1,
                failures=0, incomplete=0, finished=1, sampled_frames=8, checked_frames=8)))
            report=check_policy.report(root, check_policy.plan_options({}))
            self.assertFalse(report['integrity_errors'])
            self.assertEqual(report['environment']['status'], 'NOT_RECORDED')
            missing=check_policy.report(root, check_policy.plan_options({}), environment_required=True)
            self.assertIn('missing environment_runtime.json', missing['integrity_errors'])

    def test_assertion_abort_is_incomplete_execution_with_intact_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'check_runtime.json').write_text(json.dumps(dict(mode='transaction', assert_fatal=1,
                env_fatal=1, effective_assert_fatal=1, failures=1, incomplete=0, finished=0,
                sampled_frames=8, checked_frames=7)))
            (root/'assertions.jsonl').write_text('{"label":"T_R_ID_ORDER","edge":8}\n')
            (root/'environment_runtime.json').write_text(json.dumps(dict(env_fatal=1,
                failures=0, unrecoverable=0, finished=0)))
            (root/'environment_errors.jsonl').write_text('')
            report=check_policy.report(root, check_policy.normalize(), environment_required=True)
            self.assertFalse(report['integrity_errors'])
            self.assertFalse(report['environment']['completed'])
            self.assertEqual(report['environment']['status'], 'INCOMPLETE')
            self.assertEqual(report['groups'][1]['status'], 'FAIL')


if __name__=='__main__': unittest.main()
