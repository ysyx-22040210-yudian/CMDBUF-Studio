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
        self.assertEqual(check_policy.plan_options({}), dict(check_mode='strict', assert_fatal=True))
        self.assertEqual(model.default_plan()['check_mode'], 'transaction')
        self.assertEqual(check_policy.plan_options(dict(check_mode='transaction',assert_fatal=False)),
                         dict(check_mode='transaction',assert_fatal=False))
        for options in [dict(check_mode='auto'), dict(assert_fatal='false'), dict(assert_fatal=0)]:
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
            for label in ('A_PENDING_RECURRENCE','A_COMPARE_PENDING_CNT','A_COMPARE_REQ','A_COMPARE_RVALID'):
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


if __name__=='__main__': unittest.main()
