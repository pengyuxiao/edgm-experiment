#!/usr/bin/env python3
from pathlib import Path
import sys

# 把 src/ 加到 sys.path 里，和 03_ast_smoketest.py 保持一致
ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from edgm.ast.ast_utils import collect_interesting_nodes  # type: ignore


def find_first_clean_seed() -> Path:
    clean_dir = ROOT / "data" / "seeds" / "clean"
    c_files = sorted(clean_dir.glob("*.c"))
    if not c_files:
        print(f"[错误] 在 {clean_dir} 下没有找到任何 .c 种子文件")
        sys.exit(1)
    return c_files[0]


def main():
    seed_path = find_first_clean_seed()
    print(f"目标种子文件: {seed_path}")

    buckets = collect_interesting_nodes(str(seed_path))

    # 打印统计信息
    print("\n[统计] AST 节点数量：")
    print(f"  函数定义 (FUNCTION_DECL): {len(buckets['functions'])}")
    print(f"  for 语句 (FOR_STMT):      {len(buckets['for'])}")
    print(f"  while 语句 (WHILE_STMT):  {len(buckets['while'])}")
    print(f"  if 语句 (IF_STMT):        {len(buckets['if'])}")
    print(f"  函数调用 (CALL_EXPR):     {len(buckets['calls'])}")
    print(f"  变量声明 (VAR_DECL):      {len(buckets['vardecls'])}")

    # 顺便列出前几个用户函数名字感受一下
    print("\n[示例] 前几个用户函数：")
    for cursor in buckets["functions"][:10]:
        loc = cursor.location
        print(f"  - {cursor.spelling} (line {loc.line}, column {loc.column})")


if __name__ == "__main__":
    main()
