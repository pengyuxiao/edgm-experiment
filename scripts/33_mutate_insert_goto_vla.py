#!/usr/bin/env python3
# scripts/33_mutate_insert_goto_vla.py

import os
import sys
import argparse
import random

# 自动加入 src/ 到 sys.path（不需要手动 export PYTHONPATH）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from edgm.mutators.insert_goto_vla import insert_goto_vla  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="I16: insert goto into VLA scope (diagnostic-sensitive)"
    )
    parser.add_argument("in_file", help="输入的 seed 源文件")
    parser.add_argument("out_file", help="输出的变异后源文件")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="随机种子（不指定则使用系统随机）",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    ok = insert_goto_vla(args.in_file, args.out_file)
    if ok:
        print(f"[结果] 已生成变异文件: {args.out_file}")
    else:
        print("[结果] 未找到可用目标，未变异。")


if __name__ == "__main__":
    main()
