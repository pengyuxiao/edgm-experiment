#!/usr/bin/env python3
"""Offline, leakage-aware fixed-scale reward evaluation. Never calls an API."""

import csv
import hashlib
import json
import math
import random
import re
import statistics
import sys
import time
import tracemalloc
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from edgm.corrected_reward import cosine_distance, score_v2
from edgm.fixed_scale_reward import VERSIONS, map_distance, score as fixed_score

OUT = ROOT / "results/reward_finalization"
RUNS = ROOT / "data/corrected_runs"
NEAR_DUPLICATE_DISTANCE = 0.01
SPLIT_SEED = 73
K = 5

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()

def load_records():
    records = {}
    inventory = []
    for run in sorted(RUNS.iterdir()):
        manifest_path, archive_path, events_path = (run / name for name in
            ("manifest.json", "archive_store.jsonl", "events.jsonl"))
        if not manifest_path.exists() or not archive_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = [json.loads(x) for x in archive_path.read_text(encoding="utf-8").splitlines() if x]
        compatible = (manifest.get("provider") == "dashscope" and
            manifest.get("model") == "text-embedding-v4" and
            manifest.get("view_mode") == "program-content-v2" and
            manifest.get("embedding_pipeline_version") == "complete-chunk-v2")
        inventory.append({"run": run.name, "rows": len(rows), "compatible": compatible,
            "provider": manifest.get("provider"), "model": manifest.get("model"),
            "view_mode": manifest.get("view_mode"),
            "embedding_pipeline_version": manifest.get("embedding_pipeline_version")})
        if not compatible:
            continue
        events = [json.loads(x) for x in events_path.read_text(encoding="utf-8").splitlines() if x] if events_path.exists() else []
        seeds = {x["content_id"]: x.get("seed") for x in events if x.get("status") == "accepted"}
        for row in rows:
            if (row.get("provider") != "dashscope" or row.get("model") != "text-embedding-v4" or
                row.get("dims") != 1024 or row.get("view_mode") != "program-content-v2" or
                row.get("embedding_pipeline_version") != "complete-chunk-v2" or
                len(row.get("vec", [])) != 1024):
                raise ValueError(f"incompatible archive row: {run.name}")
            vector = [float(x) for x in row["vec"]]
            if not all(math.isfinite(x) for x in vector) or sum(x*x for x in vector) <= 1e-24:
                raise ValueError(f"invalid real vector: {run.name}")
            source = Path(row["file"])
            if not source.is_file():
                raise ValueError(f"missing source C program: {source}")
            body = re.sub(rb"^/\* EDGM_APPLIED_ARMS:.*?\*/\s*", b"", source.read_bytes(), count=1)
            item = {"view_id": row["sha1"], "vec": vector,
                "vector_sha256": sha256_bytes(json.dumps(vector, separators=(",", ":")).encode()),
                "source_sha256": sha256_bytes(body), "seed": seeds.get(row["sha1"]),
                "run": run.name, "file": str(source)}
            old = records.get(row["sha1"])
            if old:
                if old["vector_sha256"] != item["vector_sha256"]:
                    raise ValueError("same view has different embeddings")
                if old["seed"] != item["seed"]:
                    raise ValueError("same view is attributed to different seeds")
            else:
                records[row["sha1"]] = item
    if not records or any(x["seed"] is None for x in records.values()):
        raise ValueError("missing compatible views or seed provenance")
    return [records[key] for key in sorted(records)], inventory

def distances(records):
    n = len(records)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i][j] = matrix[j][i] = cosine_distance(records[i]["vec"], records[j]["vec"])
    return matrix

def grouped_split(records, matrix, seed=SPLIT_SEED):
    n = len(records)
    parent = list(range(n))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def union(i, j):
        parent[find(i)] = find(j)
    for i in range(n):
        for j in range(i + 1, n):
            if (records[i]["seed"] == records[j]["seed"] or
                records[i]["source_sha256"] == records[j]["source_sha256"] or
                matrix[i][j] <= NEAR_DUPLICATE_DISTANCE):
                union(i, j)
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    ordered = sorted(groups.values(), key=lambda g: records[min(g)]["view_id"])
    random.Random(seed).shuffle(ordered)
    a, b = round(len(ordered)*0.6), round(len(ordered)*0.8)
    if a < 2 or b <= a or b >= len(ordered):
        raise ValueError("too few independent groups for a three-way split")
    parts = {"calibration": sorted(i for g in ordered[:a] for i in g),
             "validation": sorted(i for g in ordered[a:b] for i in g),
             "test": sorted(i for g in ordered[b:] for i in g)}
    return parts, ordered

def knn_raw(i, archive, matrix):
    ds = sorted(matrix[i][j] for j in archive if j != i)
    if not ds:
        raise ValueError("KNN requires at least one reference")
    return sum(ds[:min(K, len(ds))]) / min(K, len(ds))

def percentile(xs, fraction):
    ys = sorted(xs)
    if not ys:
        return None
    pos = (len(ys)-1)*fraction
    lo = math.floor(pos)
    return ys[lo] + (ys[math.ceil(pos)]-ys[lo])*(pos-lo)

def describe(values):
    x = sorted(values)
    return {"n": len(x), "mean": statistics.mean(x) if x else None,
        "median": statistics.median(x) if x else None,
        "sd": statistics.pstdev(x) if x else None,
        "p10": percentile(x, .1), "p25": percentile(x, .25),
        "p75": percentile(x, .75), "p90": percentile(x, .9),
        "zero_fraction": sum(v == 0 for v in x)/len(x) if x else None,
        "one_fraction": sum(v == 1 for v in x)/len(x) if x else None,
        "near_boundary_fraction": sum(v <= .001 or v >= .999 for v in x)/len(x) if x else None,
        "distinct_values": len(set(x))}

def set_metrics(indices, matrix):
    if len(indices) < 2:
        return {"pairwise": None, "knn": None, "nnmin": None, "coverage_008": None}
    pairs = [matrix[i][j] for at, i in enumerate(indices) for j in indices[at+1:]]
    neighbor = [sorted(matrix[i][j] for j in indices if i != j) for i in indices]
    return {"pairwise": statistics.mean(pairs),
            "knn": statistics.mean(sum(d[:min(K,len(d))])/min(K,len(d)) for d in neighbor),
            "nnmin": statistics.mean(d[0] for d in neighbor),
            "coverage_008": sum(d[0] > .08 for d in neighbor)/len(neighbor)}

def fixed_reference_rows(indices, archive, records, matrix, scales):
    reference = [{"sha1": records[j]["view_id"], "vec": records[j]["vec"]} for j in archive]
    rows = []
    for i in indices:
        raw = knn_raw(i, archive, matrix)
        row = {"view_id": records[i]["view_id"], "source_sha256": records[i]["source_sha256"],
               "seed": records[i]["seed"], "raw_knn": raw, "archive_size": len(archive)}
        for version, scale in scales.items():
            row[version] = map_distance(raw, version, scale)
        row["corrected-loo-v2"] = score_v2(records[i]["vec"], reference, k=K,
            calibration_cap=128, sample_seed=73)["reward01"]
        rows.append(row)
    return rows

def top_random_evaluation(rows, pool, matrix, top_n=5, repeats=1000):
    top_n = min(top_n, len(pool))
    if top_n < 2:
        return None
    by_index = {r["view_id"]: i for i, r in enumerate(pool)}
    scored = {r["view_id"]: r for r in rows}
    candidate_ids = list(scored)
    result = {}
    for version in (*VERSIONS, "corrected-loo-v2"):
        top = sorted(candidate_ids, key=lambda key: (-scored[key][version], key))[:top_n]
        chosen = [by_index[key] for key in top]
        result[version] = {"view_ids": top, "metrics": set_metrics(chosen, matrix)}
    rng = random.Random(1073)
    trials = [set_metrics(rng.sample(list(by_index.values()), top_n), matrix) for _ in range(repeats)]
    result["random"] = {key: {"mean": statistics.mean(t[key] for t in trials),
        "p025": percentile([t[key] for t in trials], .025),
        "p975": percentile([t[key] for t in trials], .975)}
        for key in ("pairwise", "knn", "nnmin", "coverage_008")}
    return result

def main():
    if OUT.exists():
        raise SystemExit(f"output already exists: {OUT}")
    started = time.perf_counter()
    records, inventory = load_records()
    matrix = distances(records)
    parts, groups = grouped_split(records, matrix)
    calibration = parts["calibration"]
    calibration_raw = [knn_raw(i, calibration, matrix) for i in calibration]
    median = statistics.median(calibration_raw)
    scales = {"fixed-linear-v1": max(calibration_raw),
              "fixed-exp-v1": median / math.log(2),
              "fixed-smooth-v1": median}
    if min(scales.values()) <= 0:
        raise ValueError("calibration cannot establish a positive scale")
    config = {"schema_version": "fixed-scale-calibration-v1", "model": "text-embedding-v4",
        "dimensions": 1024, "view_mode": "program-content-v2",
        "embedding_pipeline_version": "complete-chunk-v2", "k": K,
        "scale_rules": {"fixed-linear-v1": "max(calibration leave-one-out KNN)",
            "fixed-exp-v1": "median(calibration leave-one-out KNN)/ln(2)",
            "fixed-smooth-v1": "median(calibration leave-one-out KNN)"},
        "parameters": scales,
        "calibration_view_ids": [records[i]["view_id"] for i in calibration],
        "calibration_raw_knn": calibration_raw, "split_seed": SPLIT_SEED,
        "near_duplicate_distance": NEAR_DUPLICATE_DISTANCE}
    validation = fixed_reference_rows(parts["validation"], calibration, records, matrix, scales)
    testing = fixed_reference_rows(parts["test"], calibration, records, matrix, scales)
    stages = {}
    ordered_calibration = sorted(calibration, key=lambda i: records[i]["view_id"])
    for n in sorted(set((min(5,len(calibration)), min(10,len(calibration)), len(calibration)))):
        refs = ordered_calibration[:n]
        raw = [knn_raw(i, refs, matrix) for i in parts["validation"]]
        stages[str(n)] = {"raw": describe(raw), **{v: describe([map_distance(x,v,s) for x in raw])
                                      for v,s in scales.items()}}
    sensitivity = []
    for seed in range(SPLIT_SEED, SPLIT_SEED + 10):
        split, _ = grouped_split(records, matrix, seed)
        raws = [knn_raw(i, split["calibration"], matrix) for i in split["calibration"]]
        mid = statistics.median(raws)
        sensitivity.append({"seed": seed, "calibration_n": len(raws),
            "fixed-linear-v1": max(raws), "fixed-exp-v1": mid/math.log(2),
            "fixed-smooth-v1": mid})
    reference = [{"sha1": records[j]["view_id"], "vec": records[j]["vec"]} for j in calibration]
    query = records[parts["validation"][0]]["vec"]
    timing = {}
    for version in VERSIONS:
        tracemalloc.start()
        t = time.perf_counter()
        for _ in range(30):
            fixed_score(query, reference, version=version, scale=scales[version],
                        config_hash="0"*64, k=K)
        duration = (time.perf_counter()-t)/30
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        timing[version] = {"mean_seconds_per_score": duration, "peak_traced_bytes": peak}
    t = time.perf_counter()
    for i in parts["validation"]:
        score_v2(records[i]["vec"], reference, k=K, calibration_cap=128, sample_seed=73)
    timing["corrected-loo-v2"] = {"mean_seconds_per_score": (time.perf_counter()-t)/len(parts["validation"]),
        "peak_traced_bytes": None}
    proxy = list(csv.DictReader((ROOT / "results/reward_benchmark/per_sample_fixed.csv").open(encoding="utf-8")))
    proxy_raw = [float(r["raw_knn"]) for r in proxy]
    proxy_summary = {v: describe([map_distance(x,v,s) for x in proxy_raw]) for v,s in scales.items()}
    proxy_summary["corrected-loo-v2"] = describe([float(r["loo_v2"]) for r in proxy])
    top = top_random_evaluation(testing, records, matrix)
    summary = {"target_rows": sum(x["rows"] for x in inventory if x["compatible"]),
        "target_unique_views": len(records), "source_unique": len({r["source_sha256"] for r in records}),
        "lineage_groups": len(groups), "group_sizes": sorted((len(g) for g in groups),reverse=True),
        "split_sizes": {key: len(val) for key,val in parts.items()},
        "split_group_counts": {key: len({next(g for g in groups if i in g)[0] for i in val}) for key,val in parts.items()},
        "calibration_raw": describe(calibration_raw), "scales": scales,
        "validation": {v: describe([r[v] for r in validation]) for v in (*VERSIONS,"corrected-loo-v2")},
        "test": {v: describe([r[v] for r in testing]) for v in (*VERSIONS,"corrected-loo-v2")},
        "archive_stages": stages, "parameter_sensitivity": sensitivity,
        "timing": timing, "legacy_view_proxy_807": proxy_summary,
        "top5_test": top, "runtime_seconds": time.perf_counter()-started,
        "limitations": ["Target view has only 54 unique real vectors from 29 seed lineages.",
            "The 807-vector historical cohort includes operator labels and is proxy-only.",
            "Top-N set metrics do not establish online bandit superiority."]}
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / "calibration.json").write_text(json.dumps(config,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (OUT / "inventory.json").write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (OUT / "split.json").write_text(json.dumps({"seed": SPLIT_SEED,
        "near_duplicate_distance": NEAR_DUPLICATE_DISTANCE,
        "partitions": {name:[records[i]["view_id"] for i in inds] for name,inds in parts.items()},
        "records": [{k:v for k,v in r.items() if k!="vec"} for r in records]},
        ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for name, rows in (("validation",validation),("test",testing)):
        with (OUT / f"per_sample_{name}.csv").open("w",newline="",encoding="utf-8") as f:
            writer = csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({"target_unique_views":len(records),"split_sizes":summary["split_sizes"],
        "parameters":scales,"output":str(OUT)},ensure_ascii=False))

if __name__ == "__main__":
    main()
