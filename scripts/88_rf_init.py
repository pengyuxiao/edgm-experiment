#!/usr/bin/env python3
import argparse
from pathlib import Path

def load_arms(path: str):
    arms = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            arms.append(ln)
    return arms

def default_keywords():
    # 极简、无额外依赖的 seed 特征：行数、字符数、若干关键字计数
    return ["typedef", "enum", "switch", "while", "do", "goto", "struct", "union",
            "extern", "static", "return"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms-file", default="config/arms_list.txt")
    ap.add_argument("--state", default="data/rq4/rf_state.joblib")
    args = ap.parse_args()

    import numpy as np
    from sklearn.ensemble import RandomForestRegressor
    import joblib

    arms = load_arms(args.arms_file)
    n = len(arms)
    if n == 0:
        raise SystemExit("[ERR] empty arms_list")

    # arms -> index（onehot 在 pick/update 时现算，避免 state 里塞巨大 dict）
    arm_to_idx = {a: i for i, a in enumerate(arms)}

    model = RandomForestRegressor(
        n_estimators=200,
        random_state=1,
        n_jobs=-1
    )

    st = {
        "arms": arms,
        "arm_to_idx": arm_to_idx,
        "keywords": default_keywords(),

        # dataset for incremental retrain
        "X": [],   # list[list[float]]
        "y": [],   # list[float]

        "model": model,
        "version": 1,
    }

    Path(args.state).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(st, args.state)
    print("[OK] rf init:", Path(args.state).resolve(), "arms=", n)

if __name__ == "__main__":
    main()