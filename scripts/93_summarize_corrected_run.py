#!/usr/bin/env python3
"""Read-only summary of an isolated Corrected EIDETIC run."""

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def reward_stats(events):
    values = [float(e["reward01"]) for e in events]
    return {"count": len(values),
            "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "zero_fraction": sum(x == 0 for x in values) / len(values) if values else None,
            "one_fraction": sum(x == 1 for x in values) / len(values) if values else None}

def numeric_stats(values):
    values = [float(x) for x in values if x is not None]
    return {"count": len(values), "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None}

def summarize(run_dir):
    events = read_jsonl(run_dir / "events.jsonl")
    api = read_jsonl(run_dir / "api_requests.jsonl")
    manifest = json.loads((run_dir / "manifest.json").read_text()) if (run_dir / "manifest.json").exists() else {}
    run_summary = json.loads((run_dir / "run_summary.json").read_text()) if (run_dir / "run_summary.json").exists() else {}
    accepted = [e for e in events if e.get("status") == "accepted"]
    fallback = [e for e in accepted if e.get("normalization") in
                ("empty-neutral", "empty-archive-neutral", "single-reference-neutral",
                 "small-archive-cosine-range")]
    calibrated = [e for e in accepted if e.get("normalization") == "loo-full-archive-p10-p90"]
    fixed_scale = [e for e in accepted if e.get("normalization") == "fixed-scale-cosine-knn"]
    parameter_free = [e for e in accepted if e.get("normalization") in
                      ("theoretical-cosine-range", "archive-loo-knn-midrank")]
    flat = [e for e in accepted if e.get("normalization") == "flat-reference-cosine-range"]
    starts = [e for e in api if e.get("phase") == "start"]
    finishes = [e for e in api if e.get("phase") == "finish"]
    usage = {}
    for e in finishes:
        for key, value in (e.get("usage") or {}).items():
            usage[key] = usage.get(key, 0) + value
    selected = Counter(arm for event in events for arm in event.get("selected_arms", []))
    attempted_arms = Counter(arm for event in events for arm in event.get("attempted_arms", []))
    applied = Counter(arm for event in events for arm in event.get("applied_arms", []))
    raw = [e.get("raw_knn", e.get("knn_mean_dist")) for e in accepted]
    total_selections = sum(selected.values())
    entropy = (-sum((count / total_selections) * math.log(count / total_selections)
                    for count in selected.values()) if total_selections else None)
    arm_feedback = {}
    for arm in sorted(selected):
        feedback = [float(e["reward01"]) for e in accepted if arm in e.get("selected_arms", [])]
        arm_feedback[arm] = {"selected_count": selected[arm],
            "applied_count": applied[arm], "accepted_count": len(feedback),
            "reward_sum": sum(feedback),
            "mean_reward": statistics.mean(feedback) if feedback else None}
    compiler_counts = {}
    for name in ("gcc", "clang"):
        checks = [e["compiler_checks"][name] for e in accepted
                  if name in e.get("compiler_checks", {})]
        compiler_counts[name] = {"checked": len(checks),
            "reports_error": sum(c.get("reports_error") is True for c in checks),
            "timed_out": sum(c.get("timed_out") is True for c in checks),
            "exceptions": sum(bool(c.get("exception")) for c in checks)}
    confirmed = sum(any(c.get("reports_error") is True for c in e.get("compiler_checks", {}).values())
                    for e in accepted)
    diversity = [e["diversity"] for e in accepted if "diversity" in e]
    mutation_successes = sum(e.get("mutation_rc") == 0 and e.get("status") not in
                             ("invalid_seed", "mutation_failed") for e in events)
    return {"attempts": len(events), "statuses": dict(Counter(e.get("status") for e in events)),
            "policy_id": manifest.get("policy_id"), "rng_mode": manifest.get("rng_mode"),
            "accepted": len(accepted), "compiler_error_confirmed": confirmed,
            "compiler_errors": compiler_counts, "fallback": reward_stats(fallback),
            "calibrated": reward_stats(calibrated), "fixed_scale": reward_stats(fixed_scale),
            "parameter_free": reward_stats(parameter_free),
            "flat_reference": reward_stats(flat),
            "raw_knn": numeric_stats(raw),
            "raw_knn_by_stage": {
                "fallback": numeric_stats(e.get("raw_knn", e.get("knn_mean_dist")) for e in fallback),
                "calibrated": numeric_stats(e.get("raw_knn", e.get("knn_mean_dist")) for e in calibrated),
                "flat_reference": numeric_stats(e.get("raw_knn", e.get("knn_mean_dist")) for e in flat)},
            "selection_counts": dict(selected), "attempted_arm_counts": dict(attempted_arms),
            "applied_arm_counts": dict(applied), "arm_feedback": arm_feedback,
            "selection_entropy": entropy,
            "selection_entropy_normalized_55": entropy / math.log(55) if entropy is not None else None,
            "selection_top_share": max(selected.values()) / total_selections if total_selections else None,
            "mutation_success_rate": mutation_successes / len(events) if events else None,
            "accepted_rate": len(accepted) / len(events) if events else None,
            "diversity_definition": "within_archive_fraction_nnmin_gt_tau; not held-out coverage",
            "diversity_latest": diversity[-1] if diversity else None,
            "diversity_trajectory": diversity,
            "stop_reason": run_summary.get("stop_reason"),
            "elapsed_seconds": run_summary.get("elapsed_seconds"),
            "api": {"ledger_present": (run_dir / "api_requests.jsonl").exists(),
                    "requests_started": len(starts), "requests_completed": len(finishes),
                    "successes": sum(e.get("status") == "success" for e in finishes),
                    "failures": sum(e.get("status") == "failure" for e in finishes),
                    "retries": sum(e.get("retry_index", 0) > 0 for e in starts),
                    "splits": sum(e.get("phase") == "split" for e in api),
                    "elapsed_seconds": sum(e.get("elapsed_seconds", 0) for e in finishes),
                    "usage": usage or None}}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args()
    print(json.dumps(summarize(args.run_dir), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
