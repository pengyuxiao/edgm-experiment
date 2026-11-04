import importlib.util
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm import selection_policies as policies
from edgm.diversity_monitor import projected_metrics

def load_ts():
    spec = importlib.util.spec_from_file_location("ts", ROOT / "scripts/52_ts_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

TS = load_ts()

def run_mock(run_dir, seed, arms, policy):
    return subprocess.run([sys.executable, str(ROOT / "scripts/92_corrected_eidetic.py"),
        "--run-dir", str(run_dir), "--seed", str(seed), "--arms-file", str(arms),
        "--iterations", "2", "--max-attempts", "3", "--m", "1", "--k", "5",
        "--random-seed", "73", "--calibration-seed", "73", "--policy", policy,
        "--rng-mode", "split-v1", "--provider", "mock", "--model", "framework-mock",
        "--allow-mock"], cwd=ROOT, capture_output=True, text=True)

class PolicyComparisonTests(unittest.TestCase):
    def test_discounted_update_forgets_only_evidence(self):
        state = policies.new_state("discounted-ts", ["a", "b"], TS, gamma=.5)
        policies.update("discounted-ts", state, ["a"], .8, TS)
        self.assertAlmostEqual(state["arms"]["a"]["alpha"], 1.8)
        self.assertAlmostEqual(state["arms"]["a"]["beta"], 1.2)
        policies.update("discounted-ts", state, ["b"], .2, TS)
        self.assertAlmostEqual(state["arms"]["a"]["alpha"], 1.4)
        self.assertAlmostEqual(state["arms"]["a"]["beta"], 1.1)
        self.assertAlmostEqual(state["arms"]["b"]["alpha"], 1.2)
        self.assertAlmostEqual(state["arms"]["b"]["beta"], 1.8)
        self.assertEqual(state["updates"], 2)
        self.assertEqual(state["arms"]["a"]["updates"], 1)
        with self.assertRaises(ValueError):
            policies.new_state("discounted-ts", ["a"], TS, gamma=1)

    def test_discounted_multiarm_credit_and_zero_reward(self):
        state = policies.new_state("discounted-ts", ["a", "b", "c"], TS, gamma=.9)
        policies.update("discounted-ts", state, ["a", "b"], .8, TS)
        for arm in ("a", "b"):
            self.assertAlmostEqual(state["arms"][arm]["alpha"], 1.4)
            self.assertAlmostEqual(state["arms"][arm]["beta"], 1.6)
        policies.update("discounted-ts", state, ["c"], 0, TS)
        self.assertAlmostEqual(state["arms"]["a"]["alpha"], 1.36)
        self.assertAlmostEqual(state["arms"]["a"]["beta"], 1.54)
        self.assertEqual(state["arms"]["c"]["alpha"], 1.0)
        self.assertEqual(state["arms"]["c"]["beta"], 2.0)

    def test_policy_sampling_and_beta_contract(self):
        arms = TS.load_arms(ROOT / "config/arms_list.txt")
        self.assertEqual(len(arms), 55)
        for policy in policies.POLICIES:
            state = policies.new_state(policy, arms, TS)
            first = policies.pick(policy, state, 3, random.Random(73), TS)
            second = policies.pick(policy, state, 3, random.Random(73), TS)
            self.assertEqual(first, second)
            self.assertEqual(len(set(first)), 3)
            self.assertEqual(set(state["arms"]), set(arms))
        state = policies.new_state("beta-ts", arms, TS)
        self.assertEqual(policies.pick("beta-ts", state, 2, random.Random(17), TS),
                         TS.beta_topk(state, 2, random.Random(17)))
        policies.update("beta-ts", state, arms[:2], .8, TS)
        self.assertAlmostEqual(state["arms"][arms[0]]["alpha"], 1.4)

    def test_within_archive_coverage_is_not_holdout_hit(self):
        first = projected_metrics([], [1.0, 0.0])
        self.assertIsNone(first["coverage_gt_008"])
        second = projected_metrics([{"vec": [1.0, 0.0]}], [0.0, 1.0])
        self.assertEqual(second["mean_pairwise"], 1.0)
        self.assertEqual(second["mean_nnmin"], 1.0)
        self.assertEqual(second["coverage_gt_008"], 1.0)
        third = projected_metrics([{"vec": [1.0, 0.0]}, {"vec": [0.0, 1.0]}], [1.0, 0.0])
        self.assertAlmostEqual(third["mean_pairwise"], 2/3)
        self.assertAlmostEqual(third["mean_nnmin"], 1/3)
        self.assertAlmostEqual(third["coverage_gt_008"], 1/3)
        self.assertEqual(third["coverage_definition"], "within_archive_fraction_nnmin_gt_tau")

    def test_comparison_mode_rejects_nonstandard_arm_set_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed, arms, run = root / "seed.c", root / "arms.txt", root / "run"
            seed.write_text("int main(void){return 0;}\n")
            arms.write_text("35_mutate_insert_illegal_self_nested_struct.py\n")
            p = subprocess.run([sys.executable, str(ROOT / "scripts/92_corrected_eidetic.py"),
                "--run-dir", str(run), "--seed", str(seed), "--arms-file", str(arms),
                "--m", "1", "--policy", "random", "--rng-mode", "split-v1", "--comparison-mode",
                "--provider", "mock", "--allow-mock"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn("comparison mode requires 55 arms", p.stderr)
            self.assertFalse(run.exists())

    def test_three_mock_policies_reproducible_and_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed = root / "seed.c"
            seed.write_text("int main(void){return 0;}\n")
            arms = root / "arms.txt"
            arms.write_text("35_mutate_insert_illegal_self_nested_struct.py\n")
            outputs = {}
            for policy in policies.POLICIES:
                run = root / policy
                p = run_mock(run, seed, arms, policy)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
                state = json.loads((run / "bandit_state.json").read_text())
                summary = json.loads((run / "run_summary.json").read_text())
                self.assertEqual(len(events), 2)
                self.assertEqual(state["updates"], 2)
                self.assertEqual(len((run / "archive_store.jsonl").read_text().splitlines()), 2)
                self.assertEqual([e["archive_size_before"] for e in events], [0, 1])
                self.assertEqual([e["policy_updates_before"] for e in events], [0, 1])
                self.assertEqual([e["policy_updates_after"] for e in events], [1, 2])
                self.assertEqual(summary["accepted"], 2)
                self.assertTrue(all(e["raw_knn"] >= 0 and "diversity" in e for e in events))
                self.assertTrue(all(e["selected_arms"] == e["applied_arms"] for e in events))
                self.assertEqual(json.loads((run / "manifest.json").read_text())["rng_mode"], "split-v1")
                outputs[policy] = [(e["seed"], e["mutation_seed"], e["content_id"], e["reward01"])
                                   for e in events]
            self.assertEqual(outputs["random"], outputs["beta-ts"])
            self.assertEqual(outputs["beta-ts"], outputs["discounted-ts"])
            repeat = root / "beta-repeat"
            p = run_mock(repeat, seed, arms, "beta-ts")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            events = [json.loads(line) for line in (repeat / "events.jsonl").read_text().splitlines()]
            self.assertEqual(outputs["beta-ts"], [(e["seed"], e["mutation_seed"], e["content_id"], e["reward01"])
                                                   for e in events])

if __name__ == "__main__":
    unittest.main()
