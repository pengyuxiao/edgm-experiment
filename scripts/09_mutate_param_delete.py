#!/usr/bin/env python3
from pathlib import Path

from edgm.mutators.param_delete import delete_func_param

if __name__ == "__main__":
    # 工程根目录 = 本脚本上两级
    ROOT = Path(__file__).resolve().parents[1]

    src = ROOT / "data/seeds/clean/seed_0001.c"
    dst = ROOT / "results/debug/param_delete/seed_0001.param_deleted.c"

    ok = delete_func_param(str(src), str(dst))
    if ok:
        print(f"[结果] 已生成变异文件: {dst}")
    else:
        print("[结果] 没找到合适的函数形参可以删除（本次未生成变异文件）")
