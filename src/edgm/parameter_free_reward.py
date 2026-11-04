"""Parameter-free cosine KNN rewards for the Corrected EIDETIC runner."""

import hashlib
import heapq
import json
import math
import os
import uuid
from pathlib import Path

from edgm.corrected_reward import _distance_to_units, _unit

COSINE_RANGE_VERSION = "cosine-range-v1"
EMPIRICAL_RANK_VERSION = "archive-knn-rank-v1"
VERSIONS = (COSINE_RANGE_VERSION, EMPIRICAL_RANK_VERSION)

def config_id(version, k):
    if version not in VERSIONS or k != 5:
        raise ValueError("parameter-free reward requires a known version and k=5")
    definition = {"version": version, "k": k, "distance": "cosine",
                  "view_mode": "program-content-v2",
                  "embedding_pipeline_version": "complete-chunk-v2"}
    return hashlib.sha256(json.dumps(definition, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()

def _validated_units(vec, archive):
    query = _unit(vec)
    if query is None:
        raise ValueError("zero-norm candidate embedding")
    seen = set()
    entries = []
    units = []
    for entry in archive:
        key = entry["sha1"]
        if key in seen:
            continue
        seen.add(key)
        if len(entry["vec"]) != len(vec):
            raise ValueError("archive embedding dimensions differ")
        unit = _unit(entry["vec"])
        if unit is None:
            raise ValueError("zero-norm archive embedding")
        entries.append(entry)
        units.append(unit)
    return query, entries, units

def _reference_knn(entries, units, neighbors, cache_dir):
    n = len(entries)
    ids = [entry["sha1"] for entry in entries]
    fingerprints = [hashlib.sha256(json.dumps(entry["vec"],
        separators=(",", ":")).encode()).hexdigest() for entry in entries]
    path = Path(cache_dir) / "archive_knn_rank_k5.json" if cache_dir else None
    cached = None
    if path and path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    old_ids = cached.get("archive_ids", []) if isinstance(cached, dict) else []
    old_fingerprints = cached.get("archive_fingerprints", []) if isinstance(cached, dict) else []
    valid = (cached is not None and cached.get("version") == EMPIRICAL_RANK_VERSION
        and cached.get("k") == 5 and old_ids == ids[:len(old_ids)]
        and old_fingerprints == fingerprints[:len(old_fingerprints)]
        and len(old_ids) == len(old_fingerprints) and len(old_ids) <= n)
    old_n = len(old_ids) if valid else 0
    old_topk = cached.get("topk", {}) if valid else {}
    topk = {}
    reference = []
    for i, key in enumerate(ids):
        if (i < old_n and key in old_topk and
            len(old_topk[key]) == neighbors and
            neighbors == min(5, old_n - 1)):
            nearest = heapq.nsmallest(neighbors, old_topk[key] +
                list(_distance_to_units(units[i], units[old_n:])))
        else:
            nearest = heapq.nsmallest(neighbors,
                _distance_to_units(units[i], units, exclude=i))
        topk[key] = nearest
        reference.append(sum(nearest) / len(nearest))
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        tmp.write_text(json.dumps({"version": EMPIRICAL_RANK_VERSION, "k": 5,
            "archive_ids": ids, "archive_fingerprints": fingerprints,
            "topk": topk}), encoding="utf-8")
        os.replace(tmp, path)
    return reference

def score(vec, archive, *, version, k=5, cache_dir=None):
    identity = config_id(version, k)
    query, entries, units = _validated_units(vec, archive)
    n = len(entries)
    if not n:
        raw, reward, method, neighbors = 0.0, 0.5, "empty-archive-neutral", 0
        less = equal = None
    elif version == COSINE_RANGE_VERSION:
        neighbors = min(k, n)
        nearest = heapq.nsmallest(neighbors, _distance_to_units(query, units))
        raw = sum(nearest) / neighbors
        reward = raw / 2.0
        method = "theoretical-cosine-range"
        less = equal = None
    elif n == 1:
        raw = next(_distance_to_units(query, units))
        reward, method, neighbors = (0.0 if raw == 0 else 0.5), "single-reference-neutral", 1
        less = equal = None
    else:
        neighbors = min(k, n - 1)
        nearest = heapq.nsmallest(neighbors, _distance_to_units(query, units))
        raw = sum(nearest) / neighbors
        reference = _reference_knn(entries, units, neighbors, cache_dir)
        less = sum(d < raw for d in reference)
        equal = sum(d == raw for d in reference)
        reward = 0.0 if raw == 0 else (less + equal / 2.0) / n
        method = "archive-loo-knn-midrank"
    if not math.isfinite(raw) or not 0 <= raw <= 2 or not 0 <= reward <= 1:
        raise ValueError("invalid cosine KNN reward")
    return {"reward_version": version, "reward_config_id": identity,
            "archive_size": n, "raw_knn": raw, "knn_mean_dist": raw,
            "effective_neighbors": neighbors, "rank_less": less, "rank_equal": equal,
            "reference_p10": None, "reference_p90": None,
            "reference_size": n if version == EMPIRICAL_RANK_VERSION and n >= 2 else 0,
            "normalization": method, "reward01": reward}
