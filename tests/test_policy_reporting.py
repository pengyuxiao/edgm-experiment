import importlib.util
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

summary = load("policy_summary", "93_summarize_corrected_run.py")
compare = load("policy_compare", "94_compare_corrected_policies.py")

class PolicyReportingTests(unittest.TestCase):
    def test_entropy_feedback_and_compiler_error_counts(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            events = [
                {"status": "accepted", "selected_arms": ["a"], "applied_arms": ["a"],
                 "attempted_arms": ["a"], "mutation_rc": 0, "reward01": 0.25,
                 "raw_knn": 0.3, "normalization": "empty-neutral",
                 "compiler_checks": {"gcc": {"reports_error": True},
                                     "clang": {"reports_error": False}}},
                {"status": "accepted", "selected_arms": ["b"], "applied_arms": ["b"],
                 "attempted_arms": ["b"], "mutation_rc": 0, "reward01": 0.0,
                 "raw_knn": 0.1, "normalization": "loo-full-archive-p10-p90",
                 "compiler_checks": {"gcc": {"reports_error": True},
                                     "clang": {"reports_error": True}}},
                {"status": "mutation_failed", "selected_arms": ["a"],
                 "applied_arms": [], "attempted_arms": ["a"], "mutation_rc": 1}]
            (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
            result = summary.summarize(run)
            expected = -(2/3 * math.log(2/3) + 1/3 * math.log(1/3))
            self.assertAlmostEqual(result["selection_entropy"], expected)
            self.assertAlmostEqual(result["selection_entropy_normalized_55"], expected/math.log(55))
            self.assertEqual(result["arm_feedback"]["a"]["selected_count"], 2)
            self.assertEqual(result["arm_feedback"]["a"]["accepted_count"], 1)
            self.assertEqual(result["arm_feedback"]["a"]["mean_reward"], .25)
            self.assertEqual(result["compiler_errors"]["gcc"]["reports_error"], 2)
            self.assertEqual(result["compiler_errors"]["clang"]["reports_error"], 1)
            self.assertEqual(result["raw_knn_by_stage"]["calibrated"]["mean"], .1)

    def test_equal_size_sampling_is_reproducible_and_without_replacement(self):
        matrix = compare.distance_matrix([[1, 0], [0, 1], [-1, 0]])
        first = compare.downsample(matrix, 2, repeats=1000, seed=73)
        self.assertEqual(first, compare.downsample(matrix, 2, repeats=1000, seed=73))
        self.assertEqual(compare.subset_metrics(matrix, [0, 1])["coverage_008"], 1)
        self.assertEqual(compare.downsample(matrix, 3, repeats=10)["pairwise"]["p025"], 4/3)
        with self.assertRaises(ValueError):
            compare.downsample(matrix, 4)

if __name__ == "__main__":
    unittest.main()
