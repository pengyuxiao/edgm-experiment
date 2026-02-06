#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
脚本：对单个 seed 执行 P13-adv inline 冲突变异。

用法示例：

    python3 scripts/25_mutate_func_inline_conflict_adv.py \
        data/seeds/clean/seed_0001.c \
        results/debug/func_inline_conflict_adv/seed_0001.func_inline_conflict_adv.c
"""

import argparse

from edgm.mutators.func_inline_conflict_adv import (
    mutate_func_inline_conflict_adv,
)


def main():
    ap = argparse.ArgumentParser(description="P13-adv: 函数 inline 冲突变异")
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = mutate_func_inline_conflict_adv(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 P13-adv 变异（找不到合适的函数定义）")


if __name__ == "__main__":
    main()
