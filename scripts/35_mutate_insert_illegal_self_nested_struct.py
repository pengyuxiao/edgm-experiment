#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse

# Make src/ importable without exporting PYTHONPATH
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from edgm.mutators.insert_illegal_self_nested_struct import insert_illegal_self_nested_struct


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("in_file")
    ap.add_argument("out_file")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out_file) or ".", exist_ok=True)

    ok = insert_illegal_self_nested_struct(args.in_file, args.out_file, seed=args.seed)
    if ok:
        print(f"[结果] 已生成 I18 变异文件: {os.path.abspath(args.out_file)}")
        print(f"[结果] 已生成变异文件: {args.out_file}")
    else:
        print("[结果] 未找到可用目标，未变异。")


if __name__ == "__main__":
    main()
