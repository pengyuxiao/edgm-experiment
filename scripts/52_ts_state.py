#!/usr/bin/env python3
import argparse, json, random
from pathlib import Path
from typing import Dict, Any, List

def load_state(p: Path) -> Dict[str, Any]:
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}

def save_state(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

def ensure_arms(state: Dict[str, Any], arms: List[str], window: int) -> None:
    state.setdefault("window", window)
    state.setdefault("arms", {})
    for a in arms:
        state["arms"].setdefault(a, {"history": []})  # rewards in [0,1]

def sample_theta(hist: List[float]) -> float:
    s = sum(hist)
    n = len(hist)
    alpha = 1.0 + s
    beta = 1.0 + (n - s)
    return random.betavariate(alpha, beta)

def thompson_topk(state: Dict[str, Any], k: int) -> List[str]:
    scores = []
    for arm, info in state["arms"].items():
        scores.append((sample_theta(info["history"]), arm))
    scores.sort(reverse=True)
    return [a for _, a in scores[:k]]

def update(state: Dict[str, Any], chosen: List[str], reward01: float) -> None:
    if not chosen:
        return
    w = int(state["window"])
    per = reward01 / len(chosen)
    for a in chosen:
        hist = state["arms"][a]["history"]
        hist.append(per)
        if len(hist) > w:
            del hist[:len(hist)-w]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms-file", default="config/arms_list.txt")
    ap.add_argument("--state", default="data/bandit/ts_state.json")
    ap.add_argument("--window", type=int, default=200)
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--reward", type=float, default=-1.0,
                    help="set in [0,1] to update once (reward is split across chosen arms)")
    args = ap.parse_args()

    arms = [ln.strip() for ln in Path(args.arms_file).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]

    state_path = Path(args.state)
    state = load_state(state_path)
    ensure_arms(state, arms, window=args.window)

    chosen = thompson_topk(state, k=args.k)
    print("[TS] chosen:", chosen)

    if 0.0 <= args.reward <= 1.0:
        update(state, chosen, args.reward)
        save_state(state_path, state)
        print("[OK] updated:", state_path)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())