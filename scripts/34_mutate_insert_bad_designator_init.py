# scripts/34_mutate_insert_bad_designator_init.py
from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

# ensure src/ is on sys.path (no PYTHONPATH needed)
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from edgm.mutators.insert_bad_designator_init import insert_bad_designator_init


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("in_file")
    ap.add_argument("out_file")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    outp = Path(args.out_file)
    outp.parent.mkdir(parents=True, exist_ok=True)

    seed = args.seed
    if seed is None:
        # allow external override but not required
        seed_env = os.environ.get("EDGM_SEED")
        seed = int(seed_env) if seed_env else random.randrange(1, 2**31 - 1)

    res = insert_bad_designator_init(args.in_file, args.out_file, seed=seed)
    print(f"[debug] I15-adv(upgraded): seed={res.seed}")
    if res.ok:
        print(f"[结果] 已生成 I15-adv 变异文件: {args.out_file}")
        print(f"[debug] I15-adv(upgraded): {res.reason}")
    else:
        print(f"[结果] 未找到可用目标，未变异。reason={res.reason}")


if __name__ == "__main__":
    main()
