#!/usr/bin/env python3
import argparse
from pathlib import Path

def seed_features(seed_path: str, keywords: list[str]):
    s = Path(seed_path).read_text(encoding="utf-8", errors="ignore")
    lines = s.count("\n") + 1
    chars = len(s)
    low = s.lower()
    feats = [float(lines), float(chars)]
    for kw in keywords:
        feats.append(float(low.count(kw)))
    return feats

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--arms", required=True, help="csv arms used")
    ap.add_argument("--reward", required=True, type=float)
    args = ap.parse_args()

    import joblib
    import numpy as np

    st = joblib.load(args.state)

    arms_all = st.get("arms", [])
    arm_to_idx = st.get("arm_to_idx", {})
    keywords = st.get("keywords", [])
    model = st.get("model", None)

    if not arms_all or not arm_to_idx or model is None:
        raise SystemExit("[ERR] rf_state schema invalid (missing arms/arm_to_idx/model)")

    x_seed = np.asarray(seed_features(args.seed, keywords), dtype=float)
    n_arms = len(arms_all)

    used_arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    per = float(args.reward) / max(1, len(used_arms))

    st.setdefault("X", [])
    st.setdefault("y", [])

    for a in used_arms:
        idx = arm_to_idx.get(a, None)
        if idx is None:
            continue
        one = np.zeros(n_arms, dtype=float)
        one[idx] = 1.0
        x = np.hstack([x_seed, one])
        st["X"].append(x.tolist())
        st["y"].append(float(per))

    # 数据少时不训；>=200 条再训（保持你原来的逻辑，不引入新参数）
    if len(st["y"]) >= 200:
        X = np.asarray(st["X"], dtype=float)
        y = np.asarray(st["y"], dtype=float)
        st["model"].fit(X, y)

    joblib.dump(st, args.state)
    print("[OK] rf update:", "n=", len(st["y"]))

if __name__ == "__main__":
    main()