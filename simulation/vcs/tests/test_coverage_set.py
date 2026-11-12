import itertools
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from code_coverage.set_cover import minimum_cover, union


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


if __name__ == '__main__':
    unittest.main()
