#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from edgm.mutators.delete_enum_constant import delete_enum_constant


def main() -> None:
    ap = argparse.ArgumentParser(
        description="P7: delete enum constant (keep uses)"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = delete_enum_constant(args.in_file, args.out_file)
    if ok:
        print(f"[结果] 已生成 P7 变异文件: {args.out_file}")
    else:
        print("[结果] 未找到可用的枚举常量，未变异。")


if __name__ == "__main__":
    main()