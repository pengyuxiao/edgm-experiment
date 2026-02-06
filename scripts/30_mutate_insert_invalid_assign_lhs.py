#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
30_mutate_insert_invalid_assign_lhs.py

I12: BinaryOperator（赋值）——把赋值左侧改成非左值表达式，
     触发 "lvalue required as left operand of assignment" 等编译错误。

用法示例：

  python3 scripts/30_mutate_insert_invalid_assign_lhs.py \
      data/seeds/clean/seed_0001.c \
      results/debug/insert_invalid_assign_lhs/seed_0001.insert_invalid_assign_lhs.c

然后可以：

  diff -u data/seeds/clean/seed_0001.c \
      results/debug/insert_invalid_assign_lhs/seed_0001.insert_invalid_assign_lhs.c | head -n 40

  gcc -std=c99 -O2 \
      -I src/csmithedge/csmith/runtime \
      results/debug/insert_invalid_assign_lhs/seed_0001.insert_invalid_assign_lhs.c \
      -o /tmp/mut_I12_gcc
"""

import argparse

from edgm.mutators.insert_invalid_assign_lhs import insert_invalid_assign_lhs


def main() -> None:
    ap = argparse.ArgumentParser(
        description="I12: 将赋值左侧替换为非左值表达式（insert_invalid_assign_lhs）"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_invalid_assign_lhs(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 I12 变异（可能未找到合适的赋值语句）")
    else:
        print(f"[结果] 已生成变异文件: {args.out_file}")


if __name__ == "__main__":
    main()
