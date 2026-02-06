#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
脚本：P14 变异 —— 在函数体中插入 free(函数指针) 调用。

用法示例：
    python3 scripts/26_mutate_insert_bad_free_call.py \
        data/seeds/clean/seed_0001.c \
        results/debug/insert_bad_free_call/seed_0001.insert_bad_free_call.c
"""

import argparse

from edgm.mutators.insert_bad_free_call import insert_bad_free_call


def main() -> None:
    ap = argparse.ArgumentParser(
        description="P14: 在函数体中插入错误的 free(函数指针) 调用"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_bad_free_call(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 P14 变异（可能未找到可用的函数定义）")


if __name__ == "__main__":
    main()
