#!/usr/bin/env python3
import argparse, json, math
from pathlib import Path
from typing import List

def load_vec(p: Path) -> List[float]:
    obj = json.loads(p.read_text(encoding="utf-8"))
    return obj["embedding"]

def l2(a: List[float], b: List[float]) -> float:
    s = 0.0
    for x, y in zip(a, b):
        d = x - y
        s += d*d
    return math.sqrt(s)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emb-root", default="data/corpus/emb")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--max", type=int, default=200)  # O(n^2) report, cap at 200
    ap.add_argument("--out", default="results/stats/pool_diversity.json")
    args = ap.parse_args()

    emb_root = Path(args.emb_root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(emb_root.glob("*.json"))
    if len(files) > args.max:
        files = files[:args.max]

    vecs = [load_vec(p) for p in files]
    n = len(vecs)
    if n < args.k + 1:
        raise SystemExit(f"need at least k+1 vectors, got n={n}, k={args.k}")

    knn_means = []
    for i in range(n):
        di = []
        for j in range(n):
            if i == j: continue
            di.append(l2(vecs[i], vecs[j]))
        di.sort()
        knn_means.append(sum(di[:args.k]) / args.k)

    d = sorted(knn_means)
    def pct(q: float) -> float:
        idx = int(q * (len(d) - 1))
        return d[idx]

    summary = {
        "n_used": n,
        "k": args.k,
        "min": d[0],
        "p10": pct(0.10),
        "p50": pct(0.50),
        "p90": pct(0.90),
        "max": d[-1],
    }
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("[OK] wrote", out)
    print(json.dumps(summary, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())