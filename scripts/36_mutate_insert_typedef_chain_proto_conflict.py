#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse

# 不需要 export PYTHONPATH：脚本自己把 src/ 塞进 sys.path
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from edgm.mutators.insert_typedef_chain_proto_conflict import insert_typedef_chain_proto_conflict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("in_file")
    ap.add_argument("out_file")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    ok = insert_typedef_chain_proto_conflict(args.in_file, args.out_file, seed=args.seed)
    if ok:
        print(f"[结果] 已生成 I19 变异文件: {args.out_file}")
    else:
        print("[debug] I19: no suitable prototypes; copied without mutation.")
        print(f"[结果] 已生成变异文件: {args.out_file} (未变异)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
