#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edgm.mutators.insert_struct_type_mismatch import insert_struct_type_mismatch_adv


def main():
    ap = argparse.ArgumentParser(description="I8-adv: struct mismatch via local macro shadowing")
    ap.add_argument("in_file")
    ap.add_argument("out_file")
    args = ap.parse_args()

    ok = insert_struct_type_mismatch_adv(args.in_file, args.out_file)
    if ok:
        print(f"[结果] 已生成变异文件: {args.out_file}")
    else:
        print("[结果] 未找到可用目标，未变异。")


if __name__ == "__main__":
    main()
