#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
脚本包装：调用 I9 变异，在已有数组下标表达式里用标量全局替换 base。
"""

import sys

from edgm.mutators.insert_bad_array_subscript import insert_bad_array_subscript


def main() -> None:
    if not (3 <= len(sys.argv) <= 4):
        print(
            "用法: python3 27_mutate_insert_bad_array_subscript.py "
            "<input.c> <output.c> [seed]",
            file=sys.stderr,
        )
        sys.exit(1)

    in_file = sys.argv[1]
    out_file = sys.argv[2]
    seed = int(sys.argv[3]) if len(sys.argv) == 4 else None

    ok = insert_bad_array_subscript(in_file, out_file, seed=seed)
    if not ok:
        print("[结果] 未进行 I9 变异（可能未找到可用的全局或下标表达式）")


if __name__ == "__main__":
    main()
