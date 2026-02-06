#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
I11: 将非指针表达式 x 变成 *x，触发
     indirection requires pointer operand 之类的错误。

用法示例：

  python3 scripts/29_mutate_insert_deref_nonptr_expr.py \
      data/seeds/clean/seed_0001.c \
      results/debug/insert_deref_nonptr_expr/seed_0001.insert_deref_nonptr_expr.c
"""

import argparse
import sys
from pathlib import Path

# --- 关键补丁：把 src 加到 sys.path 里 ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
# -----------------------------------------

from edgm.mutators.insert_deref_nonptr_expr import insert_deref_nonptr_expr


def main() -> None:
    ap = argparse.ArgumentParser(
        description="I11: 将非指针表达式 x 变成 *x（insert_deref_nonptr_expr）"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_deref_nonptr_expr(args.in_file, args.out_file)
    if ok:
        print(f"[结果] 已生成变异文件: {args.out_file}")
    else:
        print("[结果] 未进行 I11 变异（可能未找到合适的非指针表达式）")


if __name__ == "__main__":
    main()
