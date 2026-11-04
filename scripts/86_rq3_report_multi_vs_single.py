#!/usr/bin/env python3
import argparse, glob, json, random
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt


def cosine_dist(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 1.0
    return 1.0 - float(np.dot(a, b) / (na * nb))


def load_vecs(proc_dir):
    vecs = []
    files = sorted(glob.glob(str(Path(proc_dir) / "*.reward.json")))
    for p in files:
        try:
            o = json.load(open(p, "r", encoding="utf-8"))
            v = o.get("vec")
            if isinstance(v, list) and len(v) > 0:
                vecs.append(v)
        except Exception:
            pass
    return np.asarray(vecs, dtype=float)


def mean_pairwise_dist_sample(X, max_pairs=200000):
    n = len(X)
    if n < 2:
        return 0.0
    pairs = min(max_pairs, n * (n - 1) // 2)
    s = 0.0
    for _ in range(pairs):
        i = random.randrange(n)
        j = random.randrange(n - 1)
        if j >= i:
            j += 1
        s += cosine_dist(X[i], X[j])
    return s / pairs


def knn_and_nnmin_arrays(X, k=5):
    """
    Returns:
      knn_means[i] = mean distance from i to its k nearest neighbors
      nnmins[i]    = min distance from i to any other point
    Complexity: O(n^2). n=200 ok.
    """
    n = len(X)
    if n <= 1:
        return np.array([]), np.array([])

    k = min(k, n - 1)
    knn_means = np.zeros(n, dtype=float)
    nnmins = np.zeros(n, dtype=float)

    for i in range(n):
        ds = []
        for j in range(n):
            if i == j:
                continue
            ds.append(cosine_dist(X[i], X[j]))
        ds.sort()
        nnmins[i] = ds[0]
        knn_means[i] = sum(ds[:k]) / k

    return knn_means, nnmins


def coverage_curve_from_nnmin(nnmins, taus):
    nnmins = np.asarray(nnmins, dtype=float)
    return np.array([(nnmins > t).mean() for t in taus], dtype=float)


def pca2(X):
    X = X - X.mean(axis=0, keepdims=True)
    U, S, _Vt = np.linalg.svd(X, full_matrices=False)
    return U[:, :2] * S[:2]


def ecdf(x):
    x = np.sort(np.asarray(x, dtype=float))
    y = np.arange(1, len(x) + 1) / float(len(x))
    return x, y


def bootstrap_ci(x, stat_fn=np.mean, B=1000, seed=1):
    """
    Simple bootstrap CI for scalar statistic.
    """
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n == 0:
        return (0.0, 0.0)
    stats = []
    for _ in range(B):
        idx = rng.integers(0, n, size=n)
        stats.append(stat_fn(x[idx]))
    lo = float(np.percentile(stats, 2.5))
    hi = float(np.percentile(stats, 97.5))
    return (lo, hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--single", required=True, help="processed_chain dir for single (m=1)")
    ap.add_argument("--multi", required=True, help="processed_chain dir for multi (m=3)")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--tau", type=float, default=0.10, help="a representative tau for table row (coverage@tau)")
    ap.add_argument("--out", default="data/rq3/report")
    ap.add_argument("--no-pca", action="store_true", help="skip PCA figure if you want")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    X1 = load_vecs(args.single)
    X2 = load_vecs(args.multi)
    print("[INFO] single n=", len(X1), "multi n=", len(X2))

    # Compute arrays used by the best figures
    k = args.k
    knn1, nn1 = knn_and_nnmin_arrays(X1, k=k)
    knn2, nn2 = knn_and_nnmin_arrays(X2, k=k)

    # Core summary stats (+ bootstrap CI for mean nnmin / mean knn)
    results = {}
    for name, X, knn, nn in [("single", X1, knn1, nn1), ("multi", X2, knn2, nn2)]:
        mp = mean_pairwise_dist_sample(X)
        results[name] = {
            "n": int(len(X)),
            "mean_pairwise_cosine_dist(sampled)": float(mp),
            "mean_knn_cosine_dist": float(np.mean(knn)) if len(knn) else 0.0,
            "median_knn_cosine_dist": float(np.median(knn)) if len(knn) else 0.0,
            "mean_nnmin_cosine_dist": float(np.mean(nn)) if len(nn) else 0.0,
            "median_nnmin_cosine_dist": float(np.median(nn)) if len(nn) else 0.0,
            f"coverage_nnmin@{args.tau}": float((nn > args.tau).mean()) if len(nn) else 0.0,
        }

        # bootstrap CI (optional but very paper-friendly)
        if len(nn):
            lo, hi = bootstrap_ci(nn, stat_fn=np.mean, B=1000, seed=7)
            results[name]["mean_nnmin_ci95"] = [lo, hi]
        if len(knn):
            lo, hi = bootstrap_ci(knn, stat_fn=np.mean, B=1000, seed=9)
            results[name]["mean_knn_ci95"] = [lo, hi]

    (out / "rq3_report_summary.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("[OK] wrote", out / "rq3_report_summary.json")

    # Print a direct markdown table (you can paste to paper draft)
    def fmt_ci(ci):
        if not ci or len(ci) != 2:
            return ""
        return f"[{ci[0]:.4f}, {ci[1]:.4f}]"

    rows = []
    for name in ["single", "multi"]:
        r = results[name]
        rows.append([
            name,
            r["n"],
            f'{r["mean_pairwise_cosine_dist(sampled)"]:.4f}',
            f'{r["mean_knn_cosine_dist"]:.4f}',
            fmt_ci(r.get("mean_knn_ci95")),
            f'{r["mean_nnmin_cosine_dist"]:.4f}',
            fmt_ci(r.get("mean_nnmin_ci95")),
            f'{r[f"coverage_nnmin@{args.tau}"]:.3f}',
        ])

    print("\n=== RQ3 table (copy-paste) ===")
    print("| group | n | mean pairwise dist | mean kNN dist (k=%d) | mean kNN 95%%CI | mean nnmin dist | mean nnmin 95%%CI | coverage@tau=%.2f |" % (k, args.tau))
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        print("| %s | %d | %s | %s | %s | %s | %s | %s |" % tuple(row))

    # --------- FIG 1: ECDF of nnmin (BEST to show multi more diverse) ----------
    # x-axis: cosine distance to nearest neighbor (nnmin)
    # y-axis: ECDF = fraction of points with nnmin <= x
    x1, y1 = ecdf(nn1)
    x2, y2 = ecdf(nn2)
    plt.figure()
    plt.plot(x1, y1, label="single (m=1)")
    plt.plot(x2, y2, label="multi (m=3)")
    plt.xlabel("Nearest-neighbor cosine distance (nnmin)")
    plt.ylabel("ECDF: fraction of programs with nnmin ≤ x")
    plt.title("RQ3: Nearest-neighbor distance distribution (higher = more diverse)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out / "rq3_nnmin_ecdf.pdf")  # PDF
    print("[OK] wrote", out / "rq3_nnmin_ecdf.pdf")
    plt.close()

    # --------- FIG 2: Boxplot for nnmin + kNN mean ----------
    plt.figure()
    plt.boxplot([nn1, nn2], labels=["single (m=1)", "multi (m=3)"], showfliers=False)
    plt.ylabel("Nearest-neighbor cosine distance (nnmin)")
    plt.title("RQ3: nnmin boxplot (higher = more diverse)")
    plt.tight_layout()
    plt.savefig(out / "rq3_nnmin_box.pdf")
    print("[OK] wrote", out / "rq3_nnmin_box.pdf")
    plt.close()

    plt.figure()
    plt.boxplot([knn1, knn2], labels=["single (m=1)", "multi (m=3)"], showfliers=False)
    plt.ylabel(f"Mean cosine distance to kNN (k={k})")
    plt.title("RQ3: kNN distance boxplot (higher = less redundancy)")
    plt.tight_layout()
    plt.savefig(out / "rq3_knn_box.pdf")
    print("[OK] wrote", out / "rq3_knn_box.pdf")
    plt.close()

    # --------- FIG 3: Coverage curve over tau grid (VERY paper-friendly) ----------
    taus = np.round(np.linspace(0.02, 0.20, 10), 3)
    c1 = coverage_curve_from_nnmin(nn1, taus)
    c2 = coverage_curve_from_nnmin(nn2, taus)

    plt.figure()
    plt.plot(taus, c1, marker="o", label="single (m=1)")
    plt.plot(taus, c2, marker="o", label="multi (m=3)")
    plt.xlabel("Tau threshold (τ) on nnmin cosine distance")
    plt.ylabel("Coverage: fraction of programs with nnmin > τ")
    plt.title("RQ3: Coverage@τ (higher curve = broader exploration)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out / "rq3_coverage_curve.pdf")
    print("[OK] wrote", out / "rq3_coverage_curve.pdf")
    plt.close()

    # --------- Optional: PCA scatter (PDF) ----------
    if (not args.no_pca) and len(X1) >= 2 and len(X2) >= 2:
        Y1 = pca2(X1)
        Y2 = pca2(X2)
        plt.figure()
        plt.scatter(Y1[:, 0], Y1[:, 1], alpha=0.55, label="single (m=1)")
        plt.scatter(Y2[:, 0], Y2[:, 1], alpha=0.55, label="multi (m=3)")
        plt.legend()
        plt.xlabel("PC1 (projection on 1st principal component; unitless, sign arbitrary)")
        plt.ylabel("PC2 (projection on 2nd principal component; unitless, sign arbitrary)")
        plt.title("RQ3: PCA of embeddings (visual aid; main evidence is distance/coverage)")
        plt.tight_layout()
        plt.savefig(out / "rq3_pca_scatter.pdf")
        print("[OK] wrote", out / "rq3_pca_scatter.pdf")
        plt.close()


if __name__ == "__main__":
    main()