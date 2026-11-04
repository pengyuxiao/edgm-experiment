import json
import csv
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm.fixed_scale_reward import VERSIONS, load_config, map_distance, score

class FixedScaleRewardTests(unittest.TestCase):
    def test_bounds_determinism_and_practical_monotonicity(self):
        for version in VERSIONS:
            scores = [map_distance(x, version, .08) for x in (0, .001, .02, .04, .08, .16)]
            self.assertTrue(all(0 <= x <= 1 for x in scores))
            strictly_increasing = scores[:5] if version == "fixed-linear-v1" else scores
            self.assertTrue(all(a < b for a, b in zip(strictly_increasing, strictly_increasing[1:])))
            self.assertEqual(scores, [map_distance(x, version, .08)
                                      for x in (0, .001, .02, .04, .08, .16)])
        self.assertEqual(map_distance(.16, "fixed-linear-v1", .08), 1)
        self.assertEqual(map_distance(.20, "fixed-linear-v1", .08), 1)
        for bad in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                map_distance(bad, "fixed-smooth-v1", .08)

    def test_empty_small_duplicate_metadata_and_bad_vectors(self):
        entries = [{"sha1": "a", "vec": [1.0, 0.0], "arm": "foo"},
                   {"sha1": "a", "vec": [1.0, 0.0], "arm": "bar"}]
        for version in VERSIONS:
            empty = score([1.0, 0.0], [], version=version, scale=.08,
                          config_hash="0"*64)
            self.assertEqual(empty["reward01"], .5)
            result = score([1.0, 0.0], entries, version=version, scale=.08,
                           config_hash="0"*64)
            self.assertEqual(result["archive_size"], 1)
            self.assertEqual(result["raw_knn"], 0)
            self.assertEqual(result["reward01"], 0)
            self.assertEqual(result, score([1.0, 0.0], entries[:1], version=version,
                                           scale=.08, config_hash="0"*64))
        for vec in ([0.0, 0.0], [float("nan"), 1.0]):
            with self.assertRaises(ValueError):
                score(vec, entries, version="fixed-smooth-v1", scale=.08,
                      config_hash="0"*64)

    def test_calibration_config_and_cli_mock_integration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = {"schema_version": "fixed-scale-calibration-v1",
                      "model": "text-embedding-v4", "dimensions": 1024,
                      "view_mode": "program-content-v2",
                      "embedding_pipeline_version": "complete-chunk-v2", "k": 5,
                      "parameters": {version: .08 for version in VERSIONS}}
            path = root / "calibration.json"
            path.write_text(json.dumps(config))
            _, scale, ident = load_config(path, "fixed-smooth-v1")
            self.assertEqual(scale, .08)
            self.assertEqual(len(ident), 64)
            seed = root / "seed.c"
            seed.write_text("int main(void){return 0;}\n")
            run = root / "run"
            args = [sys.executable, str(ROOT / "scripts/92_corrected_eidetic.py"),
                "--run-dir", str(run), "--seed", str(seed),
                "--arms-file", str(root / "arms.txt"),
                "--iterations", "1", "--max-attempts", "3", "--m", "1",
                "--policy", "beta-ts", "--provider", "mock", "--allow-mock",
                "--model", "text-embedding-v4", "--reward-version", "fixed-smooth-v1",
                "--reward-scale-file", str(path)]
            (root / "arms.txt").write_text("35_mutate_insert_illegal_self_nested_struct.py\n")
            result = subprocess.run(args,cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            manifest = json.loads((run / "manifest.json").read_text())
            reward_file = next((run / "mutants").glob("*.reward.json"))
            reward = json.loads(reward_file.read_text())
            self.assertEqual(manifest["reward_config_id"], ident)
            self.assertEqual((run / "reward_scale.json").read_bytes(), path.read_bytes())
            self.assertEqual(manifest["reward_scale_file"], str(run / "reward_scale.json"))
            self.assertEqual(reward["reward_config_id"], ident)
            self.assertEqual(reward["reward_version"], "fixed-smooth-v1")
            self.assertEqual(reward["reward_scale"], .08)
            self.assertTrue(0 <= reward["reward01"] <= 1)

    def test_ieee_resolution_and_proxy_raw_ranking(self):
        with (ROOT / "results/reward_benchmark/per_sample_fixed.csv").open() as f:
            rows = list(csv.DictReader(f))
        raw = sorted(float(row["raw_knn"]) for row in rows)
        self.assertEqual(len(raw), 807)
        scales = {"fixed-linear-v1": .16936481778427095,
                  "fixed-exp-v1": .041185617326125246,
                  "fixed-smooth-v1": .028547694529224545}
        for version, scale in scales.items():
            values = [map_distance(x, version, scale) for x in raw]
            self.assertEqual(len(set(values)), len(values))
            self.assertTrue(all(a < b for a, b in zip(values, values[1:])))
            x = .04
            self.assertGreater(map_distance(math.nextafter(x, math.inf), version, scale),
                               map_distance(x, version, scale))
        self.assertEqual(map_distance(2, "fixed-linear-v1", scales["fixed-linear-v1"]), 1)
        self.assertEqual(map_distance(2, "fixed-exp-v1", scales["fixed-exp-v1"]), 1)
        self.assertLess(map_distance(2, "fixed-smooth-v1", scales["fixed-smooth-v1"]), 1)

if __name__ == "__main__":
    unittest.main()
