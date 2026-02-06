#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
I13 脚本：交换类型明显不同的函数实参（swap_call_args_mismatch）

用法示例：

  python3 scripts/31_mutate_swap_call_args_mismatch.py \
      data/seeds/clean/seed_0001.c \
      results/debug/swap_call_args_mismatch/seed_0001.swap_call_args_mismatch.c

然后你可以自己做：

  diff -u data/seeds/clean/seed_0001.c \
      results/debug/swap_call_args_mismatch/seed_0001.swap_call_args_mismatch.c | head -n 40

  gcc -std=c99 -O2 -I src/csmithedge/csmith/runtime \
      results/debug/swap_call_args_mismatch/seed_0001.swap_call_args_mismatch.c \
      -o /tmp/mut_I13_gcc

  clang -std=c99 -O2 -I src/csmithedge/csmith/runtime \
      results/debug/swap_call_args_mismatch/seed_0001.swap_call_args_mismatch.c \
      -o /tmp/mut_I13_clang
"""

import argparse
from pathlib import Path

from edgm.mutators.swap_call_args_mismatch import swap_call_args_mismatch


def main() -> None:
    ap = argparse.ArgumentParser(
        description="I13: 在函数调用中交换类型明显不同的两个实参"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = swap_call_args_mismatch(args.in_file, args.out_file)
    if ok:
        print(f"[结果] 已生成变异文件: {args.out_file}")
    else:
        print("[结果] 未进行 I13 变异（可能未找到合适的调用）")


if __name__ == "__main__":
    main()
