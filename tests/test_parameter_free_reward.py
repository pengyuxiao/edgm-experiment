import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm.parameter_free_reward import (COSINE_RANGE_VERSION, EMPIRICAL_RANK_VERSION,
                                        config_id, score)

def load_ts():
    spec = importlib.util.spec_from_file_location("ts", ROOT / "scripts/52_ts_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class ParameterFreeRewardTests(unittest.TestCase):
    def test_cosine_range_bounds_strict_raw_monotonicity_and_extremes(self):
        ref = [{"sha1": "a", "vec": [1.0, 0.0]}]
        same = score([1, 0], ref, version=COSINE_RANGE_VERSION)
        orthogonal = score([0, 1], ref, version=COSINE_RANGE_VERSION)
        opposite = score([-1, 0], ref, version=COSINE_RANGE_VERSION)
        self.assertEqual([x["raw_knn"] for x in (same, orthogonal, opposite)], [0, 1, 2])
        self.assertEqual([x["reward01"] for x in (same, orthogonal, opposite)], [0, .5, 1])
        self.assertEqual(score([1, 0], [], version=COSINE_RANGE_VERSION)["reward01"], .5)
        self.assertEqual(score([1, 0], ref*2, version=COSINE_RANGE_VERSION)["archive_size"], 1)
        self.assertEqual(score([1, 0], ref, version=COSINE_RANGE_VERSION), same)

    def test_rank_small_archive_ties_and_monotonicity(self):
        ref = [{"sha1": "a", "vec": [1.0, 0.0]},
               {"sha1": "b", "vec": [-1.0, 0.0]}]
        empty = score([1, 0], [], version=EMPIRICAL_RANK_VERSION)
        single = score([1, 0], ref[:1], version=EMPIRICAL_RANK_VERSION)
        self.assertEqual((empty["reward01"], single["reward01"]), (.5, 0))
        self.assertEqual(score([0, 1], ref, version=EMPIRICAL_RANK_VERSION)["effective_neighbors"], 1)
        self.assertEqual(score([0, 1], ref, version=EMPIRICAL_RANK_VERSION)["reward01"], 0)
        rich = [{"sha1": str(i), "vec": v} for i, v in enumerate(
            ([1,0], [0,1], [-1,0], [0,-1], [1,1], [-1,1]))]
        queries = ([1,0], [1,1], [0,1], [-1,0])
        items = [score(v, rich, version=EMPIRICAL_RANK_VERSION) for v in queries]
        order = sorted(items, key=lambda item: item["raw_knn"])
        self.assertTrue(all(a["reward01"] <= b["reward01"] for a,b in zip(order,order[1:])))
        duplicate = [{"sha1": "x", "vec": [1, 0]}, {"sha1": "y", "vec": [1, 0]}]
        tied = score([1, 0], duplicate, version=EMPIRICAL_RANK_VERSION)
        self.assertEqual((tied["raw_knn"], tied["rank_equal"], tied["reward01"]), (0, 2, 0))

    def test_incremental_cache_and_invalid_vectors(self):
        refs = [{"sha1": str(i), "vec": [math.cos(i), math.sin(i)]} for i in range(8)]
        with tempfile.TemporaryDirectory() as d:
            for n in range(2, 9):
                cached = score([.3, .9], refs[:n], version=EMPIRICAL_RANK_VERSION, cache_dir=d)
                fresh = score([.3, .9], refs[:n], version=EMPIRICAL_RANK_VERSION)
                self.assertEqual(cached, fresh)
            modified = [dict(x) for x in refs]
            modified[-1] = {"sha1": refs[-1]["sha1"], "vec": [1.0, 0.0]}
            self.assertEqual(score([.3, .9], modified, version=EMPIRICAL_RANK_VERSION, cache_dir=d),
                             score([.3, .9], modified, version=EMPIRICAL_RANK_VERSION))
        for version in (COSINE_RANGE_VERSION, EMPIRICAL_RANK_VERSION):
            for bad in ([0.0,0.0], [float("nan"),1.0], [float("inf"),1.0]):
                with self.assertRaises(ValueError):
                    score(bad, refs, version=version)
            with self.assertRaises(ValueError):
                score([1,0], [{"sha1":"z","vec":[0,0]}], version=version)
            with self.assertRaises(ValueError):
                score([1,0], [{"sha1":"z","vec":[float("nan"),1]}], version=version)
            with self.assertRaises(ValueError):
                score([1,0], [{"sha1":"z","vec":[1,0,0]}], version=version)
        self.assertNotEqual(config_id(COSINE_RANGE_VERSION,5), config_id(EMPIRICAL_RANK_VERSION,5))

    def test_beta_update_accepts_both_scores_and_mock_runner(self):
        ts = load_ts()
        for version in (COSINE_RANGE_VERSION, EMPIRICAL_RANK_VERSION):
            reward = score([0,1], [{"sha1":"a","vec":[1,0]},
                                   {"sha1":"b","vec":[-1,0]}], version=version)["reward01"]
            state = ts.new_beta_state(["arm"])
            ts.beta_update(state,["arm"],reward)
            self.assertAlmostEqual(state["arms"]["arm"]["alpha"],1+reward)
            self.assertAlmostEqual(state["arms"]["arm"]["beta"],2-reward)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed = root / "seed.c"
            seed.write_text("int main(void){return 0;}\n")
            arms = root / "arms.txt"
            arms.write_text("35_mutate_insert_illegal_self_nested_struct.py\n")
            cache_names = []
            for version in (COSINE_RANGE_VERSION, EMPIRICAL_RANK_VERSION):
                run = root / version
                p = subprocess.run([sys.executable, str(ROOT / "scripts/92_corrected_eidetic.py"),
                    "--run-dir", str(run), "--seed", str(seed), "--arms-file", str(arms),
                    "--iterations", "1", "--max-attempts", "3", "--m", "1",
                    "--provider", "mock", "--allow-mock", "--model", "text-embedding-v4",
                    "--reward-version", version], cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                reward_file = next((run / "mutants").glob("*.reward.json"))
                reward_json = json.loads(reward_file.read_text())
                manifest = json.loads((run / "manifest.json").read_text())
                self.assertEqual(reward_json["reward_config_id"], config_id(version, 5))
                self.assertEqual(manifest["reward_config_id"], config_id(version, 5))
                self.assertEqual(len((run / "archive_store.jsonl").read_text().splitlines()), 1)
                cache_names.append({p.name for p in (run / "embedding_cache").rglob("*.json")})
            self.assertTrue(cache_names[0].isdisjoint(cache_names[1]))

if __name__ == "__main__":
    unittest.main()
