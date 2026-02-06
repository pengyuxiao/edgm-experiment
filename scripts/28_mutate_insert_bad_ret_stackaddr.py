#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
27_mutate_insert_bad_ret_stackaddr.py

驱动 H2 变异：
  在 seed C 文件中，插入一个返回栈上局部变量地址的 helper 函数，
  并让某个已有指针变量接收该返回值，用于触发
  “address of stack memory associated with local variable returned” 类诊断。
"""

import argparse

from edgm.mutators.insert_bad_ret_stackaddr import insert_bad_ret_stackaddr


def main():
    ap = argparse.ArgumentParser(
        description=(
            "H2: 插入返回栈上局部变量地址的 helper 函数，并让已有指针变量接收它"
        )
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_bad_ret_stackaddr(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 H2 变异（可能未找到合适的指针变量）")


if __name__ == "__main__":
    main()
