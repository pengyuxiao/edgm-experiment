#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
脚本：对单个 seed 文件应用 P21 全局变量错误使用变异。

用法示例：

  python3 scripts/23_mutate_global_use_wrong_type.py \
      data/seeds/clean/seed_0001.c \
      results/debug/global_use_wrong_type/seed_0001.global_use_wrong_type.c

然后用 diff / gcc 检查：

  diff -u data/seeds/clean/seed_0001.c \
      results/debug/global_use_wrong_type/seed_0001.global_use_wrong_type.c | head -n 40

  gcc -std=c99 -O2 \
      -I src/csmithedge/csmith/runtime \
      results/debug/global_use_wrong_type/seed_0001.global_use_wrong_type.c \
      -o /tmp/mut_p21_use_0001
"""

import argparse

from edgm.mutators.global_use_wrong_type import mutate_global_use_wrong_type


def main():
    ap = argparse.ArgumentParser(description="P21: 全局变量错误使用（wrong-type use of global variable）")
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = mutate_global_use_wrong_type(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行变异（可能是没有合适的全局变量或函数）")


if __name__ == "__main__":
    main()
