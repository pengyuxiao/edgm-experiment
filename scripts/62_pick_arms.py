#!/usr/bin/env python3
import argparse, json, random
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=2)                 # 每轮选 1-3，默认 2
    ap.add_argument("--explore", type=float, default=0.15)      # 保留 explore
    ap.add_argument("--state", default="data/bandit/ts_state.json")
    args = ap.parse_args()

    s = json.loads(Path(args.state).read_text())
    arms = s.get("arms", {})
    window = int(s.get("window", 200))

    # explore：以一定概率从所有臂里均匀抽
    all_arms = list(arms.keys())
    if all_arms and random.random() < args.explore:
        random.shuffle(all_arms)
        for a in all_arms[:args.k]:
            print(a)
        return

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

        # “TS采样值/score”：bootstrap 采样均值（不需要先验/方差等可疑参数）
        sample = [random.choice(hist) for _ in range(len(hist))]
        theta = sum(sample) / len(sample)
        scored.append((theta, a))

    chosen = []
    # 先把空臂随机塞一些，保证探索（这不是新参数，只是避免永远不选）
    random.shuffle(empty)
    while empty and len(chosen) < args.k:
        chosen.append(empty.pop())

    scored.sort(reverse=True)
    for _, a in scored:
        if len(chosen) >= args.k:
            break
        if a not in chosen:
            chosen.append(a)

    for a in chosen:
        print(a)

if __name__ == "__main__":
    main()
