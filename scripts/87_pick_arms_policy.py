#!/usr/bin/env python3
import argparse, json, random, subprocess
from pathlib import Path

def load_arms_list(path="config/arms_list.txt"):
    arms = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            arms.append(ln)
    return arms

def pick_rand(arms, k):
    arms = list(arms)
    random.shuffle(arms)
    return arms[:k]

def ensure_ts_state(ts_state_path: str, k: int, arms_file="config/arms_list.txt"):
    """
    如果 ts_state.json 不存在，就用 52_ts_state.py 生成一个（只同步 arms，不更新 reward）。
    这样不会因为 FileNotFoundError 造成“刷屏式重试”。
    """
    p = Path(ts_state_path)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    # 调 52 初始化 state
    cmd = [
        "python3", "scripts/52_ts_state.py",
        "--arms-file", arms_file,
        "--state", str(p),
        "--window", "200",
        "--k", str(k),
        "--reward", "-1"
    ]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        raise SystemExit(f"[ERR] init ts_state failed: {ts_state_path}\n{r.stderr}")

def pick_ts(ts_state_path, k, explore):
    ensure_ts_state(ts_state_path, k)
    s = json.loads(Path(ts_state_path).read_text(encoding="utf-8"))
    arms = s.get("arms", {})
    window = int(s.get("window", 200))
    all_arms = list(arms.keys())

    if not all_arms:
        # 极端情况：state schema 不对 or arms 为空，退化成从 arms_list 随机抽
        return pick_rand(load_arms_list(), k)

    # explore: 均匀随机抽
    if random.random() < explore:
        random.shuffle(all_arms)
        return all_arms[:k]

    scored = []
    empty = []
    for a, st in arms.items():
        hist = st.get("history", [])
        if not isinstance(hist, list):
            hist = []
        hist = [float(x) for x in hist[-window:] if isinstance(x, (int, float))]
        if not hist:
            empty.append(a)
            continue

        # bootstrap TS: 从历史里 bootstrap 采样均值
        sample = [random.choice(hist) for _ in range(len(hist))]
        theta = sum(sample) / len(sample)
        scored.append((theta, a))

    chosen = []
    random.shuffle(empty)
    while empty and len(chosen) < k:
        chosen.append(empty.pop())

    scored.sort(reverse=True)
    for _, a in scored:
        if len(chosen) >= k:
            break
        if a not in chosen:
            chosen.append(a)

    if not chosen:
        # 最后兜底
        return pick_rand(load_arms_list(), k)
    return chosen

def seed_features(seed_path: str, keywords: list[str]):
    s = Path(seed_path).read_text(encoding="utf-8", errors="ignore")
    lines = s.count("\n") + 1
    chars = len(s)
    low = s.lower()
    feats = [float(lines), float(chars)]
    for kw in keywords:
        feats.append(float(low.count(kw)))
    return feats

def pick_rf(rf_state_path, seed_path, k):
    """
    RF baseline（新 schema）：
      state: {arms, arm_to_idx, keywords, X, y, model}
    若模型尚未 fit 或数据太少，则回退到 random，避免卡死/异常。
    """
    import joblib
    import numpy as np

    st = joblib.load(rf_state_path)
    arms = st.get("arms", [])
    arm_to_idx = st.get("arm_to_idx", {})
    keywords = st.get("keywords", [])
    model = st.get("model", None)

    if not arms or not arm_to_idx or model is None:
        return pick_rand(load_arms_list(), k)

    # 若没有足够训练数据，直接 random（避免未 fit 的 predict 报错）
    y = st.get("y", [])
    if not isinstance(y, list) or len(y) < 50:
        return pick_rand(arms, k)

    x_seed = np.asarray(seed_features(seed_path, keywords), dtype=float)
    n_arms = len(arms)

    X = []
    for a in arms:
        idx = arm_to_idx.get(a, None)
        if idx is None:
            continue
        one = np.zeros(n_arms, dtype=float)
        one[idx] = 1.0
        X.append(np.hstack([x_seed, one]))
    if not X:
        return pick_rand(arms, k)

    X = np.vstack(X)

    try:
        pred = model.predict(X)
    except Exception:
        # 模型没 fit 或内部错误，退化 random
        return pick_rand(arms, k)

    order = np.argsort(-pred)
    chosen = []
    for i in order:
        if len(chosen) >= k:
            break
        chosen.append(arms[int(i)])
    if not chosen:
        return pick_rand(arms, k)
    return chosen

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True, choices=["ts", "rand", "rf"])
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--explore", type=float, default=0.15)
    ap.add_argument("--ts-state", default="data/bandit/ts_state.json")
    ap.add_argument("--rf-state", default="data/rq4/rf_state.joblib")
    ap.add_argument("--seed", default="")
    args = ap.parse_args()

    if args.policy == "rand":
        chosen = pick_rand(load_arms_list(), args.k)
    elif args.policy == "ts":
        chosen = pick_ts(args.ts_state, args.k, args.explore)
    else:
        if not args.seed:
            raise SystemExit("[ERR] --seed is required for rf policy")
        chosen = pick_rf(args.rf_state, args.seed, args.k)

    for a in chosen:
        print(a)

if __name__ == "__main__":
    main()