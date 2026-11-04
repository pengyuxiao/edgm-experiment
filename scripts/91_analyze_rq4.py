#!/usr/bin/env python3
import argparse, json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

def read_trace(p):
    xs=[]
    for ln in Path(p).read_text(encoding="utf-8", errors="ignore").splitlines():
        ln=ln.strip()
        if not ln: continue
        try:
            xs.append(json.loads(ln))
        except:
            pass
    return xs

def mean_ci(a):
    a=np.asarray(a, dtype=float)
    if len(a)==0: return 0.0, (0.0,0.0)
    m=float(a.mean())
    se=float(a.std(ddof=1)/np.sqrt(len(a))) if len(a)>1 else 0.0
    lo=m-1.96*se; hi=m+1.96*se
    return m, (lo, hi)

def auc_curve(y):
    y=np.asarray(y, dtype=float)
    if len(y)==0: return 0.0
    # AUC of cumulative mean curve (simple)
    cm = np.cumsum(y)/np.arange(1,len(y)+1)
    return float(cm.sum())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--ts", required=True)
    ap.add_argument("--rand", required=True)
    ap.add_argument("--rf", required=True)
    ap.add_argument("--out", default="data/rq4/analysis")
    args=ap.parse_args()

    out=Path(args.out); out.mkdir(parents=True, exist_ok=True)

    groups={
        "ts": read_trace(args.ts),
        "rand": read_trace(args.rand),
        "rf": read_trace(args.rf),
    }

    table=[]
    curves={}
    for name, recs in groups.items():
        r=[float(x.get("reward01",0.0)) for x in recs]
        m,(lo,hi)=mean_ci(r)
        auc=auc_curve(r)
        curves[name]=r
        table.append({
            "group": name,
            "n": len(r),
            "mean_reward01": m,
            "mean_reward01_ci95": [lo,hi],
            "auc_cummean_reward": auc,
        })

    (out/"rq4_reward_table.json").write_text(json.dumps(table,indent=2,ensure_ascii=False),encoding="utf-8")

    # plot cumulative mean reward
    plt.figure()
    for name, r in curves.items():
        y=np.asarray(r, dtype=float)
        if len(y)==0: continue
        cm=np.cumsum(y)/np.arange(1,len(y)+1)
        plt.plot(np.arange(1,len(y)+1), cm, label=name)
    plt.xlabel("step")
    plt.ylabel("cumulative mean reward01")
    plt.title("RQ4: Learning Efficiency (higher is better)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out/"rq4_cummean_reward.pdf")
    print("[OK] wrote", out/"rq4_cummean_reward.pdf")
    print("[OK] wrote", out/"rq4_reward_table.json")
    print(json.dumps(table, indent=2, ensure_ascii=False))

if __name__=="__main__":
    main()