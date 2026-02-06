#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
P12-adv 脚本：全局变量 static / extern 冲突（高级版）

用法示例：

  python3 scripts/24_mutate_var_static_extern_conflict_adv.py \
      data/seeds/clean/seed_0001.c \
      results/debug/var_static_extern_conflict_adv/seed_0001.var_static_extern_conflict_adv.c

然后可以：

  diff -u data/seeds/clean/seed_0001.c \
      results/debug/var_static_extern_conflict_adv/seed_0001.var_static_extern_conflict_adv.c | head -n 40

  gcc -std=c99 -O2 \
      -I src/csmithedge/csmith/runtime \
      results/debug/var_static_extern_conflict_adv/seed_0001.var_static_extern_conflict_adv.c \
      -o /tmp/mut_p12_adv_0001
"""

import argparse

from edgm.mutators.var_static_extern_conflict_adv import (
    mutate_var_static_extern_conflict_adv,
)


def main():
    ap = argparse.ArgumentParser(
        description="P12-adv: 全局变量 static / extern 冲突（高级版）"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = mutate_var_static_extern_conflict_adv(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行变异（可能没有合适的全局变量）")


if __name__ == "__main__":
    main()
