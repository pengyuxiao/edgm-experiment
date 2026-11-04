"""Versioned diversity reward for the corrected EIDETIC baseline."""

import hashlib
import heapq
import json
import math
import os
import uuid
from pathlib import Path

VERSION = "corrected-loo-v1"
VERSION_V2 = "corrected-loo-v2"

def cosine_distance(a, b):
    if len(a) != len(b) or not a:
        raise ValueError("embedding dimensions differ or are empty")
    if not all(math.isfinite(float(x)) for x in a + b):
        raise ValueError("non-finite embedding")
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) ** 2 for x in a))
    nb = math.sqrt(sum(float(x) ** 2 for x in b))
    if na <= 1e-12 or nb <= 1e-12:
        return 1.0
    return min(2.0, max(0.0, 1.0 - dot / (na * nb)))

def percentile(xs, p):
    ys = sorted(xs)
    if not ys:
        raise ValueError("empty percentile input")
    z = (len(ys) - 1) * p / 100.0
    lo = math.floor(z)
    hi = math.ceil(z)
    return ys[lo] * (hi - z) + ys[hi] * (z - lo) if lo != hi else ys[lo]

def knn_mean(vec, refs, k):
    ds = sorted(cosine_distance(vec, x) for x in refs)
    return sum(ds[:min(k, len(ds))]) / min(k, len(ds))

def score(vec, archive, k=5, cache_dir=None, calibration_cap=128):
    """archive entries contain sha1 and vec; score precedes candidate insertion.

    A deterministic bounded reservoir calibrates against leave-one-out archive KNN.
    The cache is keyed by sampled content and parameters, so unchanged reservoirs
    are reused even when the full archive grows.
    """
    if k < 1 or calibration_cap < k + 1:
        raise ValueError("invalid k or calibration cap")
    if not vec:
        raise ValueError("empty candidate vector")
    seen = set()
    entries = []
    for entry in archive:
        key = entry["sha1"]
        if key not in seen:
            seen.add(key)
            entries.append(entry)
    if not entries:
        return {"reward_version": VERSION, "knn_mean_dist": 0.0,
                "reference_p10": None, "reference_p90": None,
                "reference_size": 0, "normalization": "empty-neutral", "reward01": 0.5}
    raw = knn_mean(vec, [e["vec"] for e in entries], k)
    sample = sorted(entries, key=lambda e: hashlib.sha256(e["sha1"].encode()).digest())[:calibration_cap]
    if len(sample) < k + 1:
        method, p10, p90, reward = "small-archive-cosine-range", None, None, raw / 2.0
    else:
        identity = [(e["sha1"], e["vec"]) for e in sample]
        key = hashlib.sha256(json.dumps([VERSION, k, identity], separators=(",", ":")).encode()).hexdigest()
        path = Path(cache_dir) / (key + ".json") if cache_dir else None
        reference = None
        if path and path.exists():
            try:
                reference = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        if reference is None:
            reference = [knn_mean(e["vec"], [other["vec"] for j, other in enumerate(sample) if j != i], k)
                         for i, e in enumerate(sample)]
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(reference), encoding="utf-8")
        p10, p90 = percentile(reference, 10), percentile(reference, 90)
        if p90 - p10 <= 1e-12:
            method, reward = "flat-reference-cosine-range", raw / 2.0
        else:
            method, reward = "loo-p10-p90", (raw - p10) / (p90 - p10)
    return {"reward_version": VERSION, "knn_mean_dist": raw,
            "reference_p10": p10, "reference_p90": p90,
            "reference_size": len(sample), "normalization": method,
            "reward01": min(1.0, max(0.0, reward))}

def _unit(vec):
    vals = [float(x) for x in vec]
    if not vals or not all(math.isfinite(x) for x in vals):
        raise ValueError("empty or non-finite embedding")
    norm = math.sqrt(sum(x * x for x in vals))
    return [x / norm for x in vals] if norm > 1e-12 else None

def _distance_to_units(query, refs, exclude=None):
    for i, ref in enumerate(refs):
        if i == exclude:
            continue
        if query is None or ref is None:
            yield 1.0
        else:
            yield min(2.0, max(0.0, 1.0 - sum(a * b for a, b in zip(query, ref))))

def _v2_result(raw, p10, p90, n, sample_n, seed, method):
    if method == "empty-neutral":
        reward = 0.5
    elif p10 is None or p90 is None or p90 - p10 <= 1e-12:
        reward = raw / 2.0
    else:
        reward = (raw - p10) / (p90 - p10)
    return {"reward_version": VERSION_V2, "archive_size": n,
            "calibration_size": sample_n, "calibration_seed": seed,
            "raw_knn": raw, "p10": p10, "p90": p90,
            "knn_mean_dist": raw, "reference_p10": p10,
            "reference_p90": p90, "reference_size": sample_n,
            "normalization": method, "reward01": min(1.0, max(0.0, reward))}

def score_v2(vec, archive, k=5, cache_dir=None, calibration_cap=128, sample_seed=0):
    """Full-archive KNN for candidate and each sampled leave-one-out reference.

    An append-only cache stores each selected reference's k smallest distances.
    Adding one archive vector updates existing references in O(C*d), while a
    newly sampled reference needs one O(N*d) full query; C <= calibration_cap.
    """
    if k < 1 or calibration_cap < k + 1 or not isinstance(sample_seed, int):
        raise ValueError("invalid k, cap, or sample seed")
    query = _unit(vec)
    seen = set()
    entries = []
    for entry in archive:
        key = entry["sha1"]
        if key not in seen:
            seen.add(key)
            entries.append(entry)
    n = len(entries)
    if not n:
        return _v2_result(0.0, None, None, 0, 0, sample_seed, "empty-neutral")
    dims = len(vec)
    if any(len(e["vec"]) != dims for e in entries):
        raise ValueError("archive embedding dimensions differ")
    units = [_unit(e["vec"]) for e in entries]
    raw = sum(heapq.nsmallest(min(k, n), _distance_to_units(query, units))) / min(k, n)
    ranking = sorted(range(n), key=lambda i: hashlib.sha256(
        f"{sample_seed}:{entries[i]['sha1']}".encode()).digest())
    chosen = ranking[:calibration_cap]
    if n < k + 1:
        return _v2_result(raw, None, None, n, len(chosen), sample_seed, "small-archive-cosine-range")

    ids = [e["sha1"] for e in entries]
    fingerprints = [hashlib.sha256(json.dumps(e["vec"], separators=(",", ":")).encode()).hexdigest()
                    for e in entries]
    path = Path(cache_dir) / f"v2_incremental_k{k}_cap{calibration_cap}_seed{sample_seed}.json" if cache_dir else None
    cached = None
    if path and path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    old_ids = cached.get("archive_ids", []) if isinstance(cached, dict) else []
    old_fingerprints = cached.get("archive_fingerprints", []) if isinstance(cached, dict) else []
    valid = (cached is not None and cached.get("version") == VERSION_V2
             and cached.get("k") == k and cached.get("cap") == calibration_cap
             and cached.get("sample_seed") == sample_seed
             and old_ids == ids[:len(old_ids)] and len(old_ids) <= n
             and old_fingerprints == fingerprints[:len(old_fingerprints)]
             and len(old_fingerprints) == len(old_ids))
    old_n = len(old_ids) if valid else 0
    old_topk = cached.get("topk", {}) if valid else {}
    topk = {}
    reference = []
    for i in chosen:
        key = ids[i]
        if i < old_n and key in old_topk and len(old_topk[key]) == min(k, old_n - 1):
            neighbors = heapq.nsmallest(k, list(old_topk[key]) +
                list(_distance_to_units(units[i], units[old_n:])))
        else:
            neighbors = heapq.nsmallest(k, _distance_to_units(units[i], units, exclude=i))
        topk[key] = neighbors
        reference.append(sum(neighbors) / len(neighbors))
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        tmp.write_text(json.dumps({"version": VERSION_V2, "k": k, "cap": calibration_cap,
            "sample_seed": sample_seed, "archive_ids": ids,
            "archive_fingerprints": fingerprints, "topk": topk}), encoding="utf-8")
        os.replace(tmp, path)
    p10, p90 = percentile(reference, 10), percentile(reference, 90)
    method = "loo-full-archive-p10-p90" if p90 - p10 > 1e-12 else "flat-reference-cosine-range"
    return _v2_result(raw, p10, p90, n, len(chosen), sample_seed, method)
