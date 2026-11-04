#!/usr/bin/env python3
"""API-free evaluation of two parameter-free online diversity rewards."""

import argparse
import csv
import importlib.util
import json
import math
import random
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm.corrected_reward import score_v2
from edgm.parameter_free_reward import (COSINE_RANGE_VERSION as A,
    EMPIRICAL_RANK_VERSION as B, score)

def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

offline = load_script("reward_finalization", "scripts/95_finalize_reward_offline.py")
ts = load_script("eidetic_ts", "scripts/52_ts_state.py")
OUT = ROOT / "results/reward_parameter_free"

def stats(xs):
    result = offline.describe(xs)
    result["minimum"] = min(xs) if xs else None
    result["maximum"] = max(xs) if xs else None
    return result

def archive_rows(records, indices):
    return [{"sha1": records[i]["view_id"], "vec": records[i]["vec"]} for i in indices]

def fixed_reference(records, indices, reference):
    archive = archive_rows(records, reference)
    rows = []
    with tempfile.TemporaryDirectory() as cache:
        for i in indices:
            vec = records[i]["vec"]
            a = score(vec, archive, version=A)
            b = score(vec, archive, version=B, cache_dir=cache)
            v2 = score_v2(vec, archive, k=5, calibration_cap=128, sample_seed=73)
            rows.append({"view_id": records[i]["view_id"], "source_sha256": records[i]["source_sha256"],
                "seed": records[i]["seed"], "archive_size": len(reference),
                "raw_knn": a["raw_knn"], "reward_A": a["reward01"],
                "rank_raw_knn": b["raw_knn"], "reward_B": b["reward01"],
                "rank_less": b["rank_less"], "rank_equal": b["rank_equal"],
                "reward_v2": v2["reward01"]})
    return rows

def ranking(rows):
    ordered = sorted(rows, key=lambda row: row["raw_knn"])
    a = [row["reward_A"] for row in ordered]
    b = [row["reward_B"] for row in ordered]
    return {"A_strictly_monotone": all(x < y for x,y in zip(a,a[1:])),
        "B_nondecreasing": all(x <= y for x,y in zip(b,b[1:])),
        "A_unique": len(set(a)), "B_unique": len(set(b)),
        "B_adjacent_ties": sum(x == y for x,y in zip(b,b[1:])),
        "B_zero_count": sum(x == 0 for x in b),
        "B_one_count": sum(x == 1 for x in b)}

def sample_choice_distribution(state, arms, draws=5000):
    rng = random.Random(20261026)
    counts = Counter()
    for _ in range(draws):
        chosen = max(arms, key=lambda arm: rng.betavariate(
            state["arms"][arm]["alpha"], state["arms"][arm]["beta"]))
        counts[chosen] += 1
    return {"draws": draws, "top_arm_probability": max(counts.values())/draws,
            "top5_probability": sum(v for _,v in counts.most_common(5))/draws,
            "selection_entropy": -sum((v/draws)*math.log(v/draws) for v in counts.values()),
            "never_selected_arm_probability": sum(v for arm,v in counts.items()
                if state["arms"][arm]["updates"] == 0)/draws}

def history_replay(run_name, arms):
    run = ROOT / "data/corrected_runs" / run_name
    events = [json.loads(line) for line in (run/"events.jsonl").read_text().splitlines()]
    archive = [json.loads(line) for line in (run/"archive_store.jsonl").read_text().splitlines()]
    accepted = [event for event in events if event.get("status") == "accepted"]
    if len(accepted) != len(archive):
        raise ValueError(f"history archive mismatch: {run_name}")
    states = {name: ts.new_beta_state(arms) for name in (A,B,"corrected-loo-v2")}
    reward_rows = []
    with tempfile.TemporaryDirectory() as cache:
        for i,(event,entry) in enumerate(zip(accepted,archive)):
            if event["content_id"] != entry["sha1"]:
                raise ValueError("history content order mismatch")
            previous = archive[:i]
            ra = score(entry["vec"], previous, version=A)
            rb = score(entry["vec"], previous, version=B, cache_dir=cache)
            if abs(ra["raw_knn"] - event["raw_knn"]) > 1e-8:
                raise ValueError("recomputed raw KNN differs from logged reward")
            values = {A: ra["reward01"], B: rb["reward01"],
                      "corrected-loo-v2": event["reward01"]}
            for name,value in values.items():
                ts.beta_update(states[name], event["selected_arms"], value)
            reward_rows.append({"run":run_name, "accepted_index":i+1, "attempt":event["attempt"],
                "selected_arms":event["selected_arms"], "archive_size":i,
                "raw_knn":ra["raw_knn"], "reward_A":values[A],
                "reward_B":values[B], "reward_v2":values["corrected-loo-v2"]})
    outputs = {}
    for name,state in states.items():
        touched = [arm for arm in arms if state["arms"][arm]["updates"]]
        means = [state["arms"][arm]["alpha"] / (state["arms"][arm]["alpha"]+
                 state["arms"][arm]["beta"]) for arm in touched]
        outputs[name] = {"updates":state["updates"], "touched_arms":len(touched),
            "posterior_mean_touched":stats(means),
            "alpha_increment_total":sum(state["arms"][arm]["alpha"]-1 for arm in arms),
            "beta_increment_total":sum(state["arms"][arm]["beta"]-1 for arm in arms),
            "sampling":sample_choice_distribution(state,arms)}
    return reward_rows, outputs

def matched_timing(records, ref_indices, query_index):
    refs = archive_rows(records,ref_indices)
    query = records[query_index]["vec"]
    result = {}
    with tempfile.TemporaryDirectory() as cache:
        for name in (A,B,"corrected-loo-v2"):
            def evaluate():
                if name == "corrected-loo-v2":
                    return score_v2(query,refs,k=5,calibration_cap=128,sample_seed=73)
                return score(query,refs,version=name,cache_dir=cache)
            start = time.perf_counter()
            evaluate()
            cold = time.perf_counter()-start
            start = time.perf_counter()
            for _ in range(20):
                evaluate()
            result[name] = {"cold_seconds":cold,
                "warm_seconds_per_score":(time.perf_counter()-start)/20}
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f"output already exists: {out}")
    started = time.perf_counter()
    records, inventory = offline.load_records()
    matrix = offline.distances(records)
    parts, groups = offline.grouped_split(records,matrix)
    reference = parts["calibration"]
    holdout = sorted(parts["validation"]+parts["test"])
    rows = fixed_reference(records,holdout,reference)
    growth = {}
    ordered = sorted(reference,key=lambda i:records[i]["view_id"])
    for n in (2,5,10,len(reference)):
        stage = fixed_reference(records,holdout,ordered[:n])
        growth[str(n)] = {"raw":stats([x["raw_knn"] for x in stage]),
            A:stats([x["reward_A"] for x in stage]),
            B:stats([x["reward_B"] for x in stage]),
            "B_unique":len({x["reward_B"] for x in stage})}
    arms = ts.load_arms(ROOT/"config/arms_list.txt")
    histories = {}
    history_rows = []
    for name in ("real_policy_cmp_20261026_001_random",
                 "real_policy_cmp_20261026_001_beta-ts",
                 "real_policy_cmp_20261026_001_discounted-ts"):
        values, result = history_replay(name, arms)
        history_rows.extend(values)
        histories[name] = {"posteriors":result,
            "rewards":{v:stats([x[key] for x in values]) for v,key in
                ((A,"reward_A"),(B,"reward_B"),("corrected-loo-v2","reward_v2"))}}
    with (ROOT/"results/reward_benchmark/per_sample_fixed.csv").open() as f:
        proxy = list(csv.DictReader(f))
    proxy_a = [float(x["raw_knn"])/2 for x in proxy]
    summary = {"compatible_rows":sum(x["rows"] for x in inventory if x["compatible"]),
        "unique_views":len(records), "lineage_groups":len(groups),
        "reference_size":len(reference), "holdout_size":len(holdout),
        "fixed_reference": {"raw":stats([x["raw_knn"] for x in rows]),
            A:stats([x["reward_A"] for x in rows]),
            B:stats([x["reward_B"] for x in rows]),
            "corrected-loo-v2":stats([x["reward_v2"] for x in rows]),
            "rank_check":ranking(rows)},
        "growth":growth, "histories":histories,
        "history_reward_distribution": {v:stats([x[key] for x in history_rows]) for v,key in
            ((A,"reward_A"),(B,"reward_B"),("corrected-loo-v2","reward_v2"))},
        "timing":matched_timing(records,reference,holdout[0]),
        "legacy_view_proxy_807_A_only":stats(proxy_a),
        "runtime_seconds":time.perf_counter()-started,
        "limitations":["Historic actions are held fixed; posterior replay is not online policy replay.",
            "The 807 historical vectors contain operator labels, and no B reference distribution is available from the CSV."]}
    out.mkdir(parents=True,exist_ok=False)
    (out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (out/"data_split.json").write_text(json.dumps({"split_seed":73,
        "near_duplicate_distance":.01,
        "reference_view_ids":[records[i]["view_id"] for i in reference],
        "holdout_view_ids":[records[i]["view_id"] for i in holdout]},
        ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for file,items in (("per_sample.csv",rows),("history_replay.csv",history_rows)):
        with (out/file).open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=list(items[0]))
            writer.writeheader()
            writer.writerows(items)
    print(json.dumps({"output":str(out),"holdout":len(rows),
        "history_accepted":len(history_rows),"runtime_seconds":summary["runtime_seconds"]},ensure_ascii=False))

if __name__ == "__main__":
    main()
