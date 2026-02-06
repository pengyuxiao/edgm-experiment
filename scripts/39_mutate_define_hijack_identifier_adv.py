#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from edgm.mutators.define_hijack_identifier_adv import insert_define_hijack_identifier_adv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("in_file")
    ap.add_argument("out_file")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    Path(args.out_file).parent.mkdir(parents=True, exist_ok=True)
    ok = insert_define_hijack_identifier_adv(args.in_file, args.out_file, seed=args.seed)
    if ok:
        print(f"[结果] 已生成 C-macro-adv 变异文件: {args.out_file}")
    else:
        print(f"[结果] C-macro-adv 未插入（已原样拷贝或跳过）: {args.out_file}")


if __name__ == "__main__":
    main()
