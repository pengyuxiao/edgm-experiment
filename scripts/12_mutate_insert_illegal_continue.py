#!/usr/bin/env python3
from pathlib import Path

from edgm.mutators.insert_illegal_continue import insert_illegal_continue


if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parents[1]

    src = ROOT / "data/seeds/clean/seed_0001.c"
    dst = ROOT / "results/debug/insert_continue/seed_0001.insert_continue.c"

    ok = insert_illegal_continue(str(src), str(dst))
    if ok:
        print(f"[结果] 已生成变异文件: {dst}")
    else:
        print("[结果] 本次没有找到合适的 CompoundStmt 可以插入非法 continue;")
