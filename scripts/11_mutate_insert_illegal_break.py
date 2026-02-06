#!/usr/bin/env python3
from pathlib import Path

from edgm.mutators.insert_illegal_break import insert_illegal_break


if __name__ == "__main__":
    # 工程根目录 = 本脚本上两级
    ROOT = Path(__file__).resolve().parents[1]

    src = ROOT / "data/seeds/clean/seed_0001.c"
    dst = ROOT / "results/debug/insert_break/seed_0001.insert_break.c"

    ok = insert_illegal_break(str(src), str(dst))
    if ok:
        print(f"[结果] 已生成变异文件: {dst}")
    else:
        print("[结果] 本次没有找到合适的 CompoundStmt 可以插入非法 break;")
