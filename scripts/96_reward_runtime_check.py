#!/usr/bin/env python3
"""Matched, API-free timing and memory check for locked reward candidates."""

import importlib.util
import json
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reward_offline", ROOT / "scripts/95_finalize_reward_offline.py")
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)
from edgm.corrected_reward import score_v2
from edgm.fixed_scale_reward import VERSIONS, score as fixed_score

def main():
    output = ROOT / "results/reward_finalization/runtime_check.json"
    if output.exists():
        raise SystemExit(f"output already exists: {output}")
    records, _ = offline.load_records()
    matrix = offline.distances(records)
    parts, _ = offline.grouped_split(records, matrix)
    calibration = json.loads((output.parent / "calibration.json").read_text())
    refs = [{"sha1": records[j]["view_id"], "vec": records[j]["vec"]}
            for j in parts["calibration"]]
    vec = records[parts["validation"][0]]["vec"]
    values = {}
    for version in (*VERSIONS, "corrected-loo-v2"):
        def evaluate():
            if version == "corrected-loo-v2":
                return score_v2(vec, refs, k=5, calibration_cap=128, sample_seed=73)
            return fixed_score(vec, refs, version=version,
                scale=calibration["parameters"][version], config_hash="0"*64, k=5)
        evaluate()
        start = time.perf_counter()
        for _ in range(20):
            evaluate()
        seconds = (time.perf_counter() - start) / 20
        tracemalloc.start()
        evaluate()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        values[version] = {"mean_seconds_per_score": seconds,
                           "peak_traced_bytes": peak}
    result = {"archive_size": len(refs), "dimensions": len(vec),
              "repetitions": 20, "timing_excludes_tracemalloc": True, "results": values}
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))

if __name__ == "__main__":
    main()
