import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm.corrected_reward import score

def script(name):
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

TS = script("52_ts_state.py")
ARM = script("60_run_arm_one.py")
REWARD = script("53_reward_one_c.py")
DRIVER = script("92_corrected_eidetic.py")

def command(*args):
    return subprocess.run([sys.executable, *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True)

class CorrectedBaselineTests(unittest.TestCase):
    def test_beta_initial_and_equal_split(self):
        state = TS.new_beta_state(["a", "b", "c"])
        self.assertEqual(TS.beta_topk(state, 2, __import__("random").Random(7)).__len__(), 2)
        self.assertEqual(state["arms"]["a"]["alpha"], 1)
        TS.beta_update(state, ["a", "b"], 0.8)
        self.assertAlmostEqual(state["arms"]["a"]["alpha"], 1.4)
        self.assertAlmostEqual(state["arms"]["a"]["beta"], 1.6)
        self.assertEqual(state["arms"]["c"]["updates"], 0)
        self.assertEqual(state["updates"], 1)
        with self.assertRaises(ValueError):
            TS.beta_update(state, ["a", "a"], 0.3)

    def test_beta_picker_reads_persisted_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            state = TS.new_beta_state(["a", "b"])
            TS.beta_update(state, ["a"], 1.0)
            path.write_text(json.dumps(state))
            p = command(ROOT / "scripts/62_pick_arms.py", "--policy", "beta_ts", "--json", "--k", 1, "--state", path)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["policy_id"], "beta_ts_eq3_v1")

    def test_reward_loo_and_boundaries(self):
        archive = [{"sha1": str(i), "vec": [1.0, 0.0]} for i in range(6)]
        with tempfile.TemporaryDirectory() as d:
            same = score([1.0, 0.0], archive, 5, d)
            far = score([0.0, 1.0], archive, 5, d)
            self.assertEqual(same["reward01"], 0.0)
            self.assertGreater(far["reward01"], same["reward01"])
            self.assertEqual(far["normalization"], "flat-reference-cosine-range")
            self.assertEqual(len(list(Path(d).glob("*.json"))), 1)
            self.assertEqual(score([1, 0], [], 5)["reward01"], 0.5)
            self.assertEqual(score([0, 1], archive[:2], 5)["normalization"], "small-archive-cosine-range")
            duplicate_id = archive + [{"sha1": "0", "vec": [0.0, 1.0]}]
            self.assertEqual(score([0, 1], duplicate_id, 5)["reference_size"], 6)

    def test_reward_nonflat_reference_and_cap(self):
        archive = [{"sha1": str(i), "vec": [1.0, float(i) / 3]} for i in range(12)]
        out = score([0.0, 1.0], archive, 5, calibration_cap=8)
        self.assertEqual(out["normalization"], "loo-p10-p90")
        self.assertEqual(out["reference_size"], 8)
        self.assertGreaterEqual(out["reward01"], 0)
        self.assertLessEqual(out["reward01"], 1)

    def test_legacy_formula_still_degenerates_at_41(self):
        distances = [i / 40 for i in range(41)]
        mean_five = sum(distances[:5]) / 5
        p10 = REWARD.percentile(distances, 10)
        p90 = REWARD.percentile(distances, 90)
        self.assertEqual(max(0, min(1, (mean_five - p10) / (p90 - p10))), 0)

    def test_failed_ts_commit_leaves_marker_and_archive_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "archive_store.jsonl").write_text("")
            class BrokenTS:
                @staticmethod
                def beta_update(*_):
                    raise RuntimeError("injected update failure")
            reward = {"content_id": "id", "reward01": 0.6}
            with self.assertRaises(RuntimeError):
                DRIVER.reward_then_commit(root, {"updates": 0}, ["a"], reward,
                                          root / "mutant.c", {}, BrokenTS)
            self.assertTrue((root / "PENDING_COMMIT.json").exists())
            self.assertEqual((root / "archive_store.jsonl").read_text(), "")

    def test_mutation_result_contracts(self):
        with tempfile.TemporaryDirectory() as d:
            inp, out = Path(d) / "in.c", Path(d) / "out.c"
            inp.write_text("before")
            out.write_text("after")
            class Result:
                def __init__(self, ok): self.ok = ok
            self.assertTrue(ARM.mutation_succeeded(None, inp, out))
            self.assertTrue(ARM.mutation_succeeded(True, inp, out))
            self.assertTrue(ARM.mutation_succeeded(Result(True), inp, out))
            self.assertFalse(ARM.mutation_succeeded(False, inp, out))
            self.assertFalse(ARM.mutation_succeeded(Result(False), inp, out))
            out.write_text("before")
            self.assertFalse(ARM.mutation_succeeded(None, inp, out))

    def test_stale_output_refused_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            inp, out = Path(d) / "in.c", Path(d) / "out.c"
            inp.write_text("int main(void){return 0;}\n")
            out.write_text("user-owned output")
            p = command(ROOT / "scripts/60_run_arm_one.py", "--arm",
                        "35_mutate_insert_illegal_self_nested_struct.py",
                        "--in", inp, "--out", out, "--strict-result")
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(out.read_text(), "user-owned output")

    def test_partial_chain_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            inp, out = Path(d) / "in.c", Path(d) / "final.c"
            inp.write_text("int main(void){return 0;}\n")
            p = command(ROOT / "scripts/70_multi_mutate_one.py", "--seed", inp,
                        "--arms", "35_mutate_insert_illegal_self_nested_struct.py,34_mutate_insert_bad_designator_init.py",
                        "--out", out, "--workdir", Path(d) / "work", "--require-complete")
            self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertFalse(out.exists())
            rec = json.loads(Path(str(out) + ".attempt.json").read_text())
            self.assertEqual(len(rec["selected_arms"]), 2)
            self.assertEqual(len(rec["attempted_arms"]), 2)
            self.assertEqual(rec["applied_arms"], ["35_mutate_insert_illegal_self_nested_struct.py"])

    def test_two_mutant_online_mock(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed = root / "seed.c"
            seed.write_text("int main(void){return 0;}\n")
            arms = root / "arms.txt"
            arms.write_text("35_mutate_insert_illegal_self_nested_struct.py\n")
            run = root / "corrected-run"
            p = command(ROOT / "scripts/92_corrected_eidetic.py", "--run-dir", run,
                        "--seed", seed, "--arms-file", arms, "--iterations", 2,
                        "--max-attempts", 3, "--m", 1, "--provider", "mock",
                        "--model", "test-mock", "--allow-mock", "--reward-version", "corrected-loo-v1")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            events = [json.loads(x) for x in (run / "events.jsonl").read_text().splitlines()]
            self.assertEqual([e["ts_updates_before"] for e in events], [0, 1])
            self.assertEqual([e["status"] for e in events], ["accepted", "accepted"])
            self.assertEqual([e["attempted_arms"] for e in events], [e["selected_arms"] for e in events])
            self.assertEqual([e["applied_arms"] for e in events], [e["selected_arms"] for e in events])
            reward_files = sorted((run / "mutants").glob("*.reward.json"))
            rewards = [json.loads(f.read_text()) for f in reward_files]
            self.assertEqual([r["archive_size"] for r in rewards], [0, 1])
            self.assertEqual(len((run / "archive_store.jsonl").read_text().splitlines()), 2)
            state = json.loads((run / "bandit_state.json").read_text())
            self.assertEqual(state["updates"], 2)
            self.assertAlmostEqual(state["arms"]["35_mutate_insert_illegal_self_nested_struct.py"]["alpha"],
                                   1 + sum(r["reward01"] for r in rewards))
            for file in (run / "mutants").glob("*.chain.c"):
                self.assertNotEqual(file.read_bytes(), seed.read_bytes())
            duplicate = command(ROOT / "scripts/53_reward_one_c.py",
                                sorted((run / "mutants").glob("*.chain.c"))[0],
                                "--reward-version", "corrected-loo-v1",
                                "--archive-store", run / "archive_store.jsonl",
                                "--cache-dir", run / "embedding_cache",
                                "--view-dir", run / "embedding_view",
                                "--calibration-cache-dir", run / "calibration_cache",
                                "--provider", "mock", "--model", "test-mock", "--allow-mock")
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("duplicate embedding view", duplicate.stderr)
            self.assertEqual(json.loads((run / "bandit_state.json").read_text())["updates"], 2)
            rerun = command(ROOT / "scripts/92_corrected_eidetic.py", "--run-dir", run,
                            "--seed", seed, "--arms-file", arms, "--iterations", 1,
                            "--m", 1, "--provider", "mock", "--allow-mock")
            self.assertNotEqual(rerun.returncode, 0)
            other = root / "another-run"
            independent = command(ROOT / "scripts/92_corrected_eidetic.py", "--run-dir", other,
                                  "--seed", seed, "--arms-file", arms, "--iterations", 1,
                                  "--m", 1, "--provider", "mock", "--model", "test-mock", "--allow-mock")
            self.assertEqual(independent.returncode, 0, independent.stderr)
            self.assertEqual(json.loads((other / "bandit_state.json").read_text())["updates"], 1)
            self.assertEqual(json.loads((run / "bandit_state.json").read_text())["updates"], 2)
            self.assertEqual(len((other / "archive_store.jsonl").read_text().splitlines()), 1)

    def test_failed_chain_never_rewards(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed = root / "seed.c"
            seed.write_text("int main(void){return 0;}\n")
            arms = root / "arms.txt"
            arms.write_text("35_mutate_insert_illegal_self_nested_struct.py\n34_mutate_insert_bad_designator_init.py\n")
            run = root / "failed-run"
            p = command(ROOT / "scripts/92_corrected_eidetic.py", "--run-dir", run,
                        "--seed", seed, "--arms-file", arms, "--iterations", 1,
                        "--max-attempts", 1, "--m", 2, "--provider", "mock",
                        "--model", "test-mock", "--allow-mock")
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertEqual(json.loads((run / "bandit_state.json").read_text())["updates"], 0)
            self.assertEqual((run / "archive_store.jsonl").read_text(), "")
            event = json.loads((run / "events.jsonl").read_text().strip())
            self.assertEqual(event["status"], "mutation_failed")
            self.assertEqual(len(event["selected_arms"]), 2)
            self.assertEqual(len(event["attempted_arms"]), 2)
            self.assertEqual(event["applied_arms"], ["35_mutate_insert_illegal_self_nested_struct.py"])

if __name__ == "__main__":
    unittest.main()
