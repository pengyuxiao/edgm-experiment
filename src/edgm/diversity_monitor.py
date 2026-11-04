"""Exact within-archive diversity metrics; independent of reward normalization."""

import statistics

from edgm.corrected_reward import cosine_distance

def projected_metrics(archive, candidate_vec, thresholds=(0.08, 0.10)):
    vectors = [entry["vec"] for entry in archive] + [candidate_vec]
    n = len(vectors)
    if n == 1:
        return {"archive_size_after": 1, "mean_pairwise": None, "mean_nnmin": None,
                "coverage_gt_008": None, "coverage_gt_010": None,
                "coverage_definition": "within_archive_fraction_nnmin_gt_tau"}
    nearest = [float("inf")] * n
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            distance = cosine_distance(vectors[i], vectors[j])
            pairs.append(distance)
            nearest[i] = min(nearest[i], distance)
            nearest[j] = min(nearest[j], distance)
    return {"archive_size_after": n, "mean_pairwise": statistics.mean(pairs),
            "mean_nnmin": statistics.mean(nearest),
            "coverage_gt_008": sum(x > thresholds[0] for x in nearest) / n,
            "coverage_gt_010": sum(x > thresholds[1] for x in nearest) / n,
            "coverage_definition": "within_archive_fraction_nnmin_gt_tau"}
