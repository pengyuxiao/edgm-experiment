#!/usr/bin/env python3
"""Read-only comparison of completed Corrected EIDETIC runs."""

import argparse
import csv
import importlib.util
import json
import math
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm.corrected_reward import cosine_distance

spec = importlib.util.spec_from_file_location("corrected_summary", ROOT / "scripts/93_summarize_corrected_run.py")
summary_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary_module)

POLICIES = ("random", "beta-ts", "discounted-ts")

def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    x = (len(values) - 1) * fraction
    lo = math.floor(x)
    return values[lo] + (values[math.ceil(x)] - values[lo]) * (x - lo)

def distance_matrix(vectors):
    n = len(vectors)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i][j] = matrix[j][i] = cosine_distance(vectors[i], vectors[j])
    return matrix

def subset_metrics(matrix, indices):
    if len(indices) < 2:
        return {"pairwise": None, "nnmin": None, "coverage_008": None}
    distances = [matrix[i][j] for pos, i in enumerate(indices) for j in indices[pos + 1:]]
    nearest = [min(matrix[i][j] for j in indices if j != i) for i in indices]
    return {"pairwise": statistics.mean(distances), "nnmin": statistics.mean(nearest),
            "coverage_008": sum(x > 0.08 for x in nearest) / len(nearest)}

def downsample(matrix, size, repeats=1000, seed=73):
    if not 1 <= size <= len(matrix) or repeats < 1:
        raise ValueError("invalid downsampling size or repeat count")
    rng = random.Random(seed)
    values = {key: [] for key in ("pairwise", "nnmin", "coverage_008")}
    for _ in range(repeats):
        metrics = subset_metrics(matrix, rng.sample(range(len(matrix)), size))
        for key, value in metrics.items():
            if value is not None:
                values[key].append(value)
    return {key: {"mean": statistics.mean(v) if v else None,
                  "p025": percentile(v, 0.025), "p975": percentile(v, 0.975)}
            for key, v in values.items()}

def inspect_run(run_dir):
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    events = summary_module.read_jsonl(run_dir / "events.jsonl")
    archive = summary_module.read_jsonl(run_dir / "archive_store.jsonl")
    accepted = [e for e in events if e.get("status") == "accepted"]
    if len(archive) != len(accepted):
        raise ValueError(f"archive/accepted mismatch: {run_dir}")
    if any(a.get("sha1") != e.get("content_id") for a, e in zip(archive, accepted)):
        raise ValueError(f"archive order/content mismatch: {run_dir}")
    if accepted and (accepted[0].get("archive_size_before") != 0 or
                     accepted[0].get("policy_updates_before") != 0):
        raise ValueError(f"run does not begin with empty online state: {run_dir}")
    return {"manifest": manifest, "summary": summary_module.summarize(run_dir),
            "events": events, "archive": archive}

def compare(run_dirs, repeats=1000, seed=73):
    runs = {policy: inspect_run(Path(run_dirs[policy])) for policy in POLICIES}
    fingerprints = {run["manifest"].get("comparison_config_sha256") for run in runs.values()}
    compilers = {json.dumps(run["manifest"].get("compiler_identities"), sort_keys=True)
                 for run in runs.values()}
    code_hashes = {json.dumps(run["manifest"].get("code_sha256"), sort_keys=True)
                   for run in runs.values()}
    if len(fingerprints) != 1 or len(compilers) != 1 or len(code_hashes) != 1:
        raise ValueError("shared config, compiler identity, or code hashes differ")
    if any(run["manifest"].get("policy") != policy or
           run["manifest"].get("comparison_mode") is not True or
           run["manifest"].get("provider") != "dashscope" for policy, run in runs.items()):
        raise ValueError("run is not a real comparison for its declared policy")
    size = min(len(run["archive"]) for run in runs.values())
    result = {"comparison_config_sha256": next(iter(fingerprints)),
              "compiler_identities": runs["random"]["manifest"]["compiler_identities"],
              "downsample_size": size, "downsample_repeats": repeats,
              "downsample_seed": seed, "policies": {}}
    for index, policy in enumerate(POLICIES):
        run = runs[policy]
        matrix = distance_matrix([item["vec"] for item in run["archive"]])
        result["policies"][policy] = {"run_dir": str(Path(run_dirs[policy]).resolve()),
            "summary": run["summary"], "full_set": subset_metrics(matrix, list(range(len(matrix)))),
            "equal_size": downsample(matrix, size, repeats, seed + index) if size else None}
    return result

def save(result, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    columns = ["policy", "attempts", "accepted", "confirmed_error", "accepted_rate",
               "raw_knn_mean", "raw_knn_median", "calibrated_count", "calibrated_reward_mean",
               "calibrated_zero_fraction", "calibrated_one_fraction", "pairwise", "nnmin",
               "coverage_008", "entropy", "entropy_normalized_55", "top_arm_share",
               "api_requests", "api_tokens", "api_elapsed_seconds", "run_elapsed_seconds"]
    with (output_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for policy, item in result["policies"].items():
            s, full = item["summary"], item["full_set"]
            usage = s["api"]["usage"] or {}
            writer.writerow(dict(zip(columns, [policy, s["attempts"], s["accepted"],
                s["compiler_error_confirmed"], s["accepted_rate"], s["raw_knn"]["mean"],
                s["raw_knn"]["median"], s["calibrated"]["count"], s["calibrated"]["mean"],
                s["calibrated"]["zero_fraction"], s["calibrated"]["one_fraction"],
                full["pairwise"], full["nnmin"], full["coverage_008"], s["selection_entropy"],
                s["selection_entropy_normalized_55"], s["selection_top_share"],
                s["api"]["requests_started"], usage.get("total_tokens"),
                s["api"]["elapsed_seconds"], s["elapsed_seconds"]])))
    lines = ["# Corrected EIDETIC 三策略在线小规模对照", "",
             f"共同配置指纹：`{result['comparison_config_sha256']}`。",
             f"等样本量 n={result['downsample_size']}，重复 {result['downsample_repeats']} 次；"
             "区间仅为本次运行的下采样不确定性，不是跨运行算法置信区间。", "",
             "| 策略 | 尝试 | 接受 | 确认 error | 校准 reward 均值 | raw KNN 均值 | Pairwise | NNMin | 组内 Coverage@0.08 | H/ln55 | 请求 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def fmt(value):
        return "NA" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)
    for policy, item in result["policies"].items():
        s, full = item["summary"], item["full_set"]
        lines.append("| " + " | ".join(map(fmt, [policy, s["attempts"], s["accepted"],
            s["compiler_error_confirmed"], s["calibrated"]["mean"], s["raw_knn"]["mean"],
            full["pairwise"], full["nnmin"], full["coverage_008"],
            s["selection_entropy_normalized_55"], s["api"]["requests_started"]])) + " |")
    lines += ["", "回退、正式校准、平坦参考阶段的 reward 分布及等样本量结果见 `comparison.json`。",
              "Coverage@0.08 是入选集合内最近邻距离大于 0.08 的比例，不是留出集覆盖。",
              "单个随机种子不能证明策略优越性、探索停滞或非平稳适应性。", ""]
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    for policy in POLICIES:
        ap.add_argument("--" + policy, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=73)
    args = ap.parse_args()
    result = compare({policy: getattr(args, policy.replace("-", "_")) for policy in POLICIES},
                     args.repeats, args.seed)
    save(result, args.out)
    print(json.dumps({"output": args.out, "common_config": result["comparison_config_sha256"],
                      "downsample_size": result["downsample_size"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
