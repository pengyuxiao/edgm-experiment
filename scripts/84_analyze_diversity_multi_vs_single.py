#!/usr/bin/env python3
import argparse, glob, json, random
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

def cosine_dist(a, b):
    a=np.asarray(a, dtype=float); b=np.asarray(b, dtype=float)
    na=np.linalg.norm(a); nb=np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return 1.0
    return 1.0 - float(np.dot(a,b)/(na*nb))

def load_vecs(proc_dir):
    vecs=[]
    files=sorted(glob.glob(str(Path(proc_dir)/"*.reward.json")))
    for p in files:
        try:
            o=json.load(open(p,"r",encoding="utf-8"))
            v=o.get("vec")
            if isinstance(v,list) and len(v)>0:
                vecs.append(v)
        except Exception:
            pass
    return np.asarray(vecs, dtype=float)

def mean_pairwise_dist(X, max_pairs=200000):
    n=len(X)
    if n<2: return 0.0
    pairs=min(max_pairs, n*(n-1)//2)
    s=0.0
    for _ in range(pairs):
        i=random.randrange(n)
        j=random.randrange(n-1)
        if j>=i: j+=1
        s += cosine_dist(X[i], X[j])
    return s/pairs

def knn_stats(X, k=5):
    n=len(X)
    if n<=1: return 0.0, 0.0
    k=min(k, n-1)
    knn_means=[]
    nn_mins=[]
    for i in range(n):
        ds=[]
        for j in range(n):
            if i==j: continue
            ds.append(cosine_dist(X[i], X[j]))
        ds.sort()
        nn_mins.append(ds[0])
        knn_means.append(sum(ds[:k])/k)
    return float(np.mean(knn_means)), float(np.median(knn_means)), float(np.mean(nn_mins)), float(np.median(nn_mins))

def coverage_tau_from_nnmin(nnmins, tau):
    if not nnmins: return 0.0
    nnmins=np.asarray(nnmins, dtype=float)
    return float(np.mean(nnmins > tau))

def pca2(X):
    X = X - X.mean(axis=0, keepdims=True)
    U,S,Vt = np.linalg.svd(X, full_matrices=False)
    return U[:, :2]*S[:2]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--single", required=True)
    ap.add_argument("--multi", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--tau", type=float, default=0.2)
    ap.add_argument("--out", default="data/rq3/analysis")
    args=ap.parse_args()

    out=Path(args.out); out.mkdir(parents=True, exist_ok=True)

    X1=load_vecs(args.single)
    X2=load_vecs(args.multi)
    print("[INFO] single n=", len(X1), "multi n=", len(X2))

    results={}
    for name, X in [("single", X1), ("multi", X2)]:
        n=len(X)
        mp=mean_pairwise_dist(X)
        knn_mean, knn_med, nn_mean, nn_med = knn_stats(X, k=args.k)

        # coverage uses nearest-neighbor min distance
        # recompute nnmins quickly from knn_stats path: do it again but store mins
        nnmins=[]
        for i in range(n):
            best=1e9
            for j in range(n):
                if i==j: continue
                best=min(best, cosine_dist(X[i], X[j]))
            nnmins.append(best)
        cov=coverage_tau_from_nnmin(nnmins, args.tau)

        results[name]={
            "n": int(n),
            "mean_pairwise_cosine_dist": float(mp),
            "mean_knn_cosine_dist": float(knn_mean),
            "median_knn_cosine_dist": float(knn_med),
            "mean_nnmin_cosine_dist": float(nn_mean),
            "median_nnmin_cosine_dist": float(nn_med),
            f"coverage_nnmin@{args.tau}": float(cov),
        }

    (out/"rq3_diversity_summary.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("[OK] wrote", out/"rq3_diversity_summary.json")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    # PCA plot
    if len(X1)>=2 and len(X2)>=2:
        Y1=pca2(X1); Y2=pca2(X2)
        plt.figure()
        plt.scatter(Y1[:,0], Y1[:,1], alpha=0.55, label="single (m=1)")
        plt.scatter(Y2[:,0], Y2[:,1], alpha=0.55, label="multi (m=3)")
        plt.legend()
        plt.xlabel("PC1"); plt.ylabel("PC2")
        plt.title("RQ3: Embedding PCA (single vs multi)")
        plt.tight_layout()
        plt.savefig(out/"rq3_pca_scatter.png", dpi=220)
        print("[OK] wrote", out/"rq3_pca_scatter.png")

if __name__=="__main__":
    main()