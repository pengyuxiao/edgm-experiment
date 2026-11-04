#!/usr/bin/env python3
import argparse, json, random
from pathlib import Path
from typing import Dict, Any, List, Optional

def load_state(p: Path) -> Dict[str, Any]:
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}

def save_state(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

def load_arms(path: str) -> List[str]:
    arms = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            arms.append(ln)
    return arms

def ensure_arms(state: Dict[str, Any], arms: List[str], window: int) -> bool:
    changed = False
    if state.get("window") != window:
        state["window"] = window
        changed = True

    if "arms" not in state or not isinstance(state["arms"], dict):
        state["arms"] = {}
        changed = True

    for a in arms:
        if a not in state["arms"]:
            state["arms"][a] = {"history": []}
            changed = True
        else:
            if "history" not in state["arms"][a] or not isinstance(state["arms"][a].get("history"), list):
                state["arms"][a]["history"] = []
                changed = True
    return changed

def sample_theta(hist: List[float]) -> float:
    # reward in [0,1] treated as fractional successes
    s = sum(hist)
    n = len(hist)
    alpha = 1.0 + s
    beta = 1.0 + (n - s)
    return random.betavariate(alpha, beta)

def thompson_topk(state: Dict[str, Any], k: int) -> List[str]:
    scores = []
    for arm, info in state["arms"].items():
        hist = info.get("history", [])
        if not isinstance(hist, list):
            hist = []
        h = [float(x) for x in hist if isinstance(x, (int, float))]
        scores.append((sample_theta(h), arm))
    scores.sort(reverse=True)
    return [a for _, a in scores[:k]]

def parse_chosen_csv(chosen_csv: str) -> List[str]:
    xs = [x.strip() for x in chosen_csv.split(",") if x.strip()]
    # de-dup but keep order
    seen = set()
    out = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def update(state: Dict[str, Any], chosen: List[str], reward01: float, split: bool) -> None:
    if not chosen:
        return
    w = int(state.get("window", 200))

    if split:
        per = float(reward01) / max(1, len(chosen))
    else:
        per = float(reward01)

    for a in chosen:
        if a not in state["arms"]:
            # tolerate unknown arm (e.g., arms list changed)
            state["arms"][a] = {"history": []}
        hist = state["arms"][a]["history"]
        hist.append(float(per))
        if len(hist) > w:
            del hist[:len(hist) - w]

def new_beta_state(arms: List[str]) -> Dict[str, Any]:
    if not arms or len(set(arms)) != len(arms):
        raise ValueError("arms must be nonempty and unique")
    return {"policy_id": "beta_ts_eq3_v1", "arms": {a: {"alpha": 1.0, "beta": 1.0,
            "updates": 0} for a in arms}, "updates": 0}

def beta_topk(state: Dict[str, Any], k: int, rng=None) -> List[str]:
    if state.get("policy_id") != "beta_ts_eq3_v1":
        raise ValueError("not a corrected Beta TS state")
    rng = rng or random
    if not 1 <= k <= len(state["arms"]):
        raise ValueError("k outside arm count")
    scores = [(rng.betavariate(float(v["alpha"]), float(v["beta"])), a)
              for a, v in state["arms"].items()]
    return [a for _, a in sorted(scores, reverse=True)[:k]]

def beta_update(state: Dict[str, Any], chosen: List[str], reward01: float) -> None:
    if state.get("policy_id") != "beta_ts_eq3_v1":
        raise ValueError("not a corrected Beta TS state")
    if not chosen or len(set(chosen)) != len(chosen) or not 0 <= reward01 <= 1:
        raise ValueError("invalid arms or reward")
    if any(a not in state["arms"] for a in chosen):
        raise ValueError("selected arm missing from state")
    share = reward01 / len(chosen)
    for arm in chosen:
        state["arms"][arm]["alpha"] += share
        state["arms"][arm]["beta"] += 1.0 - share
        state["arms"][arm]["updates"] += 1
    state["updates"] += 1

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms-file", default="config/arms_list.txt")
    ap.add_argument("--state", default="data/bandit/ts_state.json")
    ap.add_argument("--window", type=int, default=200)
    ap.add_argument("--k", type=int, default=2)

    ap.add_argument("--reward", type=float, default=-1.0,
                    help="set in [0,1] to update once. Otherwise only sync arms+window and SAVE state.")

    ap.add_argument("--chosen", default="",
                    help="csv of actually-applied arms. If set, update will use these arms instead of resampling.")
    ap.add_argument("--split", action="store_true",
                    help="if set, split reward across chosen arms (NOT recommended for your RQ4). Default: full credit.")
    args = ap.parse_args()

    arms = load_arms(args.arms_file)
    state_path = Path(args.state)

    state = load_state(state_path)
    changed = ensure_arms(state, arms, window=args.window)

    # for printing / picking (optional usage)
    picked = thompson_topk(state, k=args.k)
    print("[TS] picked:", picked)

    if 0.0 <= args.reward <= 1.0:
        if args.chosen.strip():
            chosen = parse_chosen_csv(args.chosen)
        else:
            chosen = picked
        update(state, chosen, float(args.reward), split=args.split)
        changed = True

    if changed or (not state_path.exists()):
        save_state(state_path, state)
        print("[OK] saved:", state_path.resolve())

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
