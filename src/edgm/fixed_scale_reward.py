"""Fixed-scale KNN rewards; legacy and LOO implementations remain separate."""

import hashlib
import heapq
import json
import math
from pathlib import Path

from edgm.corrected_reward import _distance_to_units, _unit

VERSIONS = ("fixed-linear-v1", "fixed-exp-v1", "fixed-smooth-v1")

def config_id(config, version):
    payload = {"version": version, "config": config}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def load_config(path, version):
    if version not in VERSIONS:
        raise ValueError("unknown fixed reward version")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if (config.get("schema_version") != "fixed-scale-calibration-v1" or
        config.get("model") != "text-embedding-v4" or config.get("dimensions") != 1024 or
        config.get("view_mode") != "program-content-v2" or
        config.get("embedding_pipeline_version") != "complete-chunk-v2" or
        config.get("k") != 5):
        raise ValueError("incompatible fixed-scale calibration config")
    scale = config.get("parameters", {}).get(version)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("fixed reward scale must be finite and positive")
    return config, float(scale), config_id(config, version)

def map_distance(raw, version, scale):
    if not math.isfinite(raw) or raw < 0:
        raise ValueError("raw KNN distance must be finite and nonnegative")
    if version not in VERSIONS or not math.isfinite(scale) or scale <= 0:
        raise ValueError("invalid fixed reward version or scale")
    if version == "fixed-linear-v1":
        reward = min(1.0, raw / scale)
    elif version == "fixed-exp-v1":
        reward = -math.expm1(-raw / scale)
    else:
        reward = raw / (raw + scale)
    return min(1.0, max(0.0, reward))

def score(vec, archive, *, version, scale, config_hash, k=5):
    if k != 5 or not isinstance(config_hash, str) or len(config_hash) != 64:
        raise ValueError("fixed-scale score requires k=5 and a calibration config ID")
    query = _unit(vec)
    if query is None:
        raise ValueError("zero-norm candidate embedding")
    seen = set()
    refs = []
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
        refs.append(unit)
    if refs:
        nearest = heapq.nsmallest(min(k, len(refs)), _distance_to_units(query, refs))
        raw = sum(nearest) / len(nearest)
        reward = map_distance(raw, version, scale)
        method = "fixed-scale-cosine-knn"
    else:
        raw, reward, method = 0.0, 0.5, "empty-archive-neutral"
    return {"reward_version": version, "reward_config_id": config_hash,
            "reward_scale": scale, "reward_scale_unit": "cosine_distance",
            "archive_size": len(refs), "raw_knn": raw, "knn_mean_dist": raw,
            "reference_p10": None, "reference_p90": None,
            "reference_size": 0, "normalization": method, "reward01": reward}
