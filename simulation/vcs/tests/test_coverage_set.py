import itertools
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from code_coverage.set_cover import minimum_cover, union
from fsdb_formal import coverage_export


class MinimumCoverageSetTests(unittest.TestCase):
    def test_greedy_is_not_a_minimality_proof(self):
        # The first set has greatest initial gain, yet the other two suffice.
        masks = [0b001111, 0b010011, 0b101100, 0b010000, 0b100000]
        result = minimum_cover(masks)
        self.assertTrue(result['optimal'])
        self.assertEqual(result['selected'], [1, 2])
        self.assertEqual(result['lower_bound'], 2)
        self.assertGreater(result['nodes'], 0)

    def test_matches_exhaustive_small_corpora(self):
        rng = random.Random(671)
        for _ in range(180):
            masks = [rng.getrandbits(10) for _ in range(rng.randrange(12))]
            target = union(masks)
            expected = next(size for size in range(len(masks)+1)
                            if any(union(group) == target for group in itertools.combinations(masks, size)))
            result = minimum_cover(masks)
            self.assertTrue(result['optimal'])
            self.assertEqual(result['upper_bound'], expected)
            self.assertEqual(union(masks[i] for i in result['selected']), target)

    def test_ten_thousand_distinct_candidates(self):
        width = 12
        full = (1 << width)-1
        masks = [mask << (group*width) for group in range(50)
                 for mask in [full] + list(range(1, 200))]
        self.assertEqual(len(masks), 10000)
        result = minimum_cover(masks)
        self.assertTrue(result['optimal'])
        self.assertEqual(result['upper_bound'], 50)
        self.assertEqual(result['distinct_profiles'], 10000)

    def test_deadline_reports_bounds_and_a_feasible_set(self):
        masks = [0b001111, 0b010011, 0b101100, 0b010000, 0b100000]
        with patch('code_coverage.set_cover.monotonic', side_effect=(0, 1, 1)):
            result = minimum_cover(masks, seconds=0.5)
        self.assertFalse(result['optimal'])
        self.assertLess(result['lower_bound'], result['upper_bound'])
        self.assertEqual(union(masks[i] for i in result['selected']), union(masks))


class SelectedFsdbExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.out = root / 'results'
        self.destination = root / 'export folder'
        self.eda = coverage_export.eda
        output_override = patch.object(self.eda, 'OUT', self.out)
        output_override.start()
        self.addCleanup(output_override.stop)
        # Different batches can contain the same case and seed.
        self.run = dict(id='tc_random_seed_1', seed=1, status='PASS')
        self.cases = []
        for batch in ('first', 'second'):
            regression = self.out / 'regressions' / batch
            folder = regression / 'cases' / self.run['id']
            folder.mkdir(parents=True)
            self.eda.write_json(regression / 'summary.json', dict(results=[self.run]))
            self.cases.append(folder)
        self.eda.write_json(self.out / 'coverage_analysis' / 'set' / 'result.json', dict(
            verified=True, solver=dict(upper_bound=2), batches=['first', 'second'],
            selected=[dict(batch=batch, id=self.run['id'], seed=1) for batch in ('first', 'second')]))
        (self.cases[0] / 'waves.fsdb').write_bytes(b'original FSDB')

    def test_archived_only_export_does_not_load_tools(self):
        (self.cases[1] / 'waves.fsdb').write_bytes(b'second FSDB')
        with patch.object(self.eda.platform_config, 'tool_env') as environment:
            folder = coverage_export.export_fsdb('set', 'only_archived', self.destination, 120)
        environment.assert_not_called()
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, {
            'pattern_0000_tc_random_seed_1.fsdb': b'original FSDB',
            'pattern_0001_tc_random_seed_1.fsdb': b'second FSDB',
        })

    def test_exports_only_original_and_verified_replay_fsdb(self):
        wave_root = self.out / 'coverage_fsdb_waves' / 'mixed' / 'pattern_1'
        wave_root.mkdir(parents=True)
        (wave_root / 'waves.fsdb').write_bytes(b'replayed FSDB')
        with patch.object(self.eda.platform_config, 'tool_env', return_value={}), \
             patch.object(self.eda, 'replay_case', return_value=(dict(directory=str(wave_root)), True)) as replay:
            folder = coverage_export.export_fsdb('set', 'mixed', self.destination, 120)
            with self.assertRaises(FileExistsError):
                coverage_export.export_fsdb('set', 'mixed', self.destination, 120)
        replay.assert_called_once_with(self.run, self.cases[1], wave_root, {}, 120, waves=True, convert_vcd=False)
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, {
            'pattern_0000_tc_random_seed_1.fsdb': b'original FSDB',
            'pattern_0001_tc_random_seed_1.fsdb': b'replayed FSDB',
        })
        self.assertFalse((self.cases[1] / 'waves.fsdb').exists())

    def test_an_inexact_replay_leaves_no_partial_export(self):
        with patch.object(self.eda.platform_config, 'tool_env', return_value={}), \
             patch.object(self.eda, 'replay_case', return_value=({}, False)):
            with self.assertRaisesRegex(RuntimeError, '与原始结果不一致'):
                coverage_export.export_fsdb('set', 'failed', self.destination, 120)
        self.assertEqual(list(self.destination.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
