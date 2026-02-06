# scripts/43_mutate_move_decl_after_first_use_adv.py
from pathlib import Path
import sys

# Ensure project root is on sys.path so "import src...." works
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse
from src.edgm.mutators.move_decl_after_first_use_adv import mutate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--keep-k", type=int, default=2)
    args = ap.parse_args()

    mutate(args.infile, args.outfile, seed=args.seed, keep_k=args.keep_k)


if __name__ == "__main__":
    main()
