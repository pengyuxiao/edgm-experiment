#!/usr/bin/env python3
from pathlib import Path

from edgm.mutators.func_delete import delete_called_function


def main():
    repo_root = Path(__file__).resolve().parents[1]

    src = repo_root / "data/seeds/clean/seed_0001.c"
    dst = repo_root / "results/debug/func_delete/seed_0001.func_deleted.c"

    ok = delete_called_function(str(src), str(dst))
    if ok:
        print(f"[结果] 已生成变异文件: {dst}")
    else:
        print("[结果] 未找到合适的函数可删（可能这个 seed 没有普通函数定义？）")


if __name__ == "__main__":
    main()
