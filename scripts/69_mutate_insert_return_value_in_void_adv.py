# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from edgm.mutators.insert_return_value_in_void_adv import (
    insert_return_value_in_void_adv,
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="I21: insert conditional return value in void function (advanced)"
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_return_value_in_void_adv(args.in_file, args.out_file)
    if ok:
        print(f"[结果] 已生成 I21 变异文件: {args.out_file}")
    else:
        print("[结果] 未找到可用的 void 函数，未变异。")


if __name__ == "__main__":
    main()