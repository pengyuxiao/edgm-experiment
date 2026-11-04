import hashlib
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
from edgm.corrected_reward import knn_mean, percentile, score_v2

def module(name):
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

REWARD = module("53_reward_one_c.py")
VIEW = module("72_preprocess_for_embedding.py")

def command(*args):
    return subprocess.run([sys.executable, *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True)

class CorrectedV2Tests(unittest.TestCase):
    def test_full_archive_reference_and_sampling(self):
        archive = [{"sha1": str(i), "vec": [1.0, i / 5.0, (i % 3) / 7.0]}
                   for i in range(12)]
        candidate = [0.3, 0.8, 0.2]
        cap, seed, k = 6, 19, 2
        result = score_v2(candidate, archive, k, calibration_cap=cap, sample_seed=seed)
        sample = sorted(archive, key=lambda e: hashlib.sha256(f"{seed}:{e['sha1']}".encode()).digest())[:cap]
        reference = [knn_mean(e["vec"], [x["vec"] for x in archive if x["sha1"] != e["sha1"]], k)
                     for e in sample]
        self.assertAlmostEqual(result["raw_knn"], knn_mean(candidate, [e["vec"] for e in archive], k))
        self.assertAlmostEqual(result["p10"], percentile(reference, 10))
        self.assertAlmostEqual(result["p90"], percentile(reference, 90))
        self.assertEqual(result["calibration_size"], cap)
        self.assertEqual(result["reward_version"], "corrected-loo-v2")
        self.assertAlmostEqual(result["p10"], score_v2(candidate, list(reversed(archive)), k,
                                           calibration_cap=cap, sample_seed=seed)["p10"])

    def test_incremental_cache_equals_fresh(self):
        archive = [{"sha1": str(i), "vec": [1.0, i / 6.0, (i % 4) / 7.0]}
                   for i in range(10)]
        with tempfile.TemporaryDirectory() as d:
            score_v2([0, 1, 0], archive[:7], 2, d, 5, 7)
            grown = score_v2([0, 1, 0], archive, 2, d, 5, 7)
            fresh = score_v2([0, 1, 0], archive, 2, None, 5, 7)
            for field in ("raw_knn", "p10", "p90", "reward01"):
                self.assertAlmostEqual(grown[field], fresh[field])
            cache = json.loads(next(Path(d).glob("v2_*.json")).read_text())
            self.assertEqual(len(cache["archive_ids"]), 10)
            changed = [dict(e) for e in archive]
            changed[2] = {"sha1": changed[2]["sha1"], "vec": [0.0, 0.0, 1.0]}
            cached_changed = score_v2([0, 1, 0], changed, 2, d, 5, 7)
            fresh_changed = score_v2([0, 1, 0], changed, 2, None, 5, 7)
            self.assertAlmostEqual(cached_changed["p10"], fresh_changed["p10"])

    def test_small_flat_and_duplicate_vectors(self):
        archive = [{"sha1": str(i), "vec": [1.0, 0.0]} for i in range(6)]
        self.assertEqual(score_v2([1, 0], [], 5)["reward01"], 0.5)
        self.assertEqual(score_v2([0, 1], archive[:2], 5)["normalization"], "small-archive-cosine-range")
        same = score_v2([1, 0], archive, 5)
        far = score_v2([0, 1], archive, 5)
        self.assertEqual(same["reward01"], 0)
        self.assertEqual(far["normalization"], "flat-reference-cosine-range")
        self.assertGreater(far["reward01"], same["reward01"])
        self.assertEqual(score_v2([0, 1], archive + [{"sha1": "0", "vec": [0, 1]}], 5)["archive_size"], 6)
        self.assertEqual(score_v2([0, 0], archive, 5)["raw_knn"], 1.0)

    def test_complete_chunk_coverage_and_weighting(self):
        for text in ("short", "x" * 7000, "abcdefghij " * 1000, "longtoken" * 4500):
            spans = REWARD.split_text_complete(text, 2000, 80, 7000, 200)
            self.assertEqual(spans[0][0], 0)
            self.assertEqual(spans[-1][1], len(text))
            self.assertTrue(all(0 < end - start <= 7000 for start, end in spans))
            self.assertTrue(all(spans[i+1][0] <= spans[i][1] and spans[i+1][1] > spans[i][1]
                                for i in range(len(spans)-1)))
            if len(text) > 7000:
                self.assertGreater(len(spans), 1)
        text = "abcdefghij " * 1000
        emb = REWARD.embed_complete(text, "mock", "test", None)
        self.assertEqual(sum(emb["chunk_weights"]), len(text))
        self.assertNotEqual(emb["vec"], REWARD.embed_complete(text[:-5] + "TAIL!", "mock", "test", None)["vec"])
        self.assertEqual(REWARD._weighted_vec([([0.0, 0.0], 9), ([1.0, 1.0], 1)]), [0.1, 0.1])
        with self.assertRaises(ValueError):
            REWARD.split_text_complete("x", 5, 5, 7, 2)

    def test_program_view_omits_operator_metadata(self):
        body = "int edgm_injected_error = missing_symbol;\nint main(void){return edgm_injected_error;}\n"
        first = "/* EDGM_APPLIED_ARMS: arm_a.py */\n" + body
        second = "/* EDGM_APPLIED_ARMS: arm_b.py */\n" + body
        old_a = VIEW.build_program_view(first, 7800)
        old_b = VIEW.build_program_view(second, 7800)
        clean_a = VIEW.build_program_view(first, 7800, "program-content-v2")
        clean_b = VIEW.build_program_view(second, 7800, "program-content-v2")
        self.assertNotEqual(old_a, old_b)
        self.assertEqual(clean_a, clean_b)
        self.assertNotIn("EDGM_APPLIED_ARMS", clean_a)
        self.assertIn("edgm_injected_error", clean_a)
        self.assertIn("missing_symbol", clean_a)

    def test_pipeline_cache_and_view_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "program.c"
            src.write_text("/* EDGM_APPLIED_ARMS: arm_a.py */\nint main(void){return missing_symbol;}\n")
            args = [ROOT / "scripts/53_reward_one_c.py", src,
                    "--provider", "mock", "--model", "test", "--allow-mock",
                    "--archive-store", root / "archive.jsonl",
                    "--cache-dir", root / "cache", "--view-dir", root / "view",
                    "--calibration-cache-dir", root / "calibration"]
            old = command(*args, "--reward-version", "corrected-loo-v1")
            new = command(*args, "--reward-version", "corrected-loo-v2")
            self.assertEqual(old.returncode, 0, old.stderr)
            self.assertEqual(new.returncode, 0, new.stderr)
            a, b = json.loads(old.stdout), json.loads(new.stdout)
            self.assertEqual(a["embedding_pipeline_version"], "legacy-chunk-v1")
            self.assertEqual(b["embedding_pipeline_version"], "complete-chunk-v2")
            self.assertNotEqual(a["content_id"], b["content_id"])
            self.assertNotEqual(a["view_file"], b["view_file"])
            self.assertEqual(len(list((root / "cache").rglob("*.json"))), 2)
            same_view_new_pipeline = command(*args, "--reward-version", "corrected-loo-v2",
                                             "--view-mode", "legacy-operator-v1")
            self.assertEqual(same_view_new_pipeline.returncode, 0, same_view_new_pipeline.stderr)
            c = json.loads(same_view_new_pipeline.stdout)
            self.assertEqual(a["content_id"], c["content_id"])
            self.assertNotEqual(a["embedding_pipeline_version"], c["embedding_pipeline_version"])
            self.assertEqual(len(list((root / "cache").rglob("*.json"))), 3)

    def test_v2_two_mutant_online_mock(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seed = root / "seed.c"
            seed.write_text("int main(void){return 0;}\n")
            arms = root / "arms.txt"
            arms.write_text("35_mutate_insert_illegal_self_nested_struct.py\n")
            run = root / "v2-run"
            p = command(ROOT / "scripts/92_corrected_eidetic.py", "--run-dir", run,
                        "--seed", seed, "--arms-file", arms, "--iterations", 2,
                        "--max-attempts", 3, "--m", 1, "--provider", "mock",
                        "--model", "test-mock-v2", "--allow-mock")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            events = [json.loads(x) for x in (run / "events.jsonl").read_text().splitlines()]
            rewards = [json.loads(p.read_text()) for p in sorted((run / "mutants").glob("*.reward.json"))]
            self.assertEqual([e["ts_updates_before"] for e in events], [0, 1])
            self.assertEqual([r["archive_size"] for r in rewards], [0, 1])
            self.assertEqual([e["selected_arms"] for e in events], [e["applied_arms"] for e in events])
            self.assertEqual([r["reward_version"] for r in rewards], ["corrected-loo-v2"] * 2)
            manifest = json.loads((run / "manifest.json").read_text())
            self.assertEqual(set(manifest["compiler_identities"]), {"gcc", "clang"})
            for event in events:
                self.assertTrue(event["meets_acceptance_condition"])
                self.assertEqual(set(event["compiler_checks"]), {"gcc", "clang"})
                for check in event["compiler_checks"].values():
                    self.assertTrue(Path(check["path"]).is_file())
                    self.assertTrue(check["version"])
                    self.assertTrue(Path(check["stderr_path"]).is_file())
                    self.assertFalse(check["timed_out"])
            self.assertEqual([e["small_archive_fallback"] for e in events], [True, True])
            self.assertEqual(manifest["chunk_max_chars"], 7000)
            self.assertEqual([r["chunk_max_chars"] for r in rewards], [7000, 7000])
            self.assertEqual(len((run / "archive_store.jsonl").read_text().splitlines()), 2)
            self.assertEqual(json.loads((run / "bandit_state.json").read_text())["updates"], 2)

if __name__ == "__main__":
    unittest.main()
