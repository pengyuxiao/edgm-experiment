#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import argparse
import sys

# 关键：保证项目根目录在 sys.path（避免 src 找不到）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.edgm.mutators.binary_op_incompatible_adv import mutate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("in_path")
    ap.add_argument("out_path")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    mutate(args.in_path, args.out_path, seed=args.seed)


if __name__ == "__main__":
    main()
