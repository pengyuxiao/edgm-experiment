#!/usr/bin/env python3
from pathlib import Path

from edgm.mutators.prune_struct_to_forward_decl import prune_struct_to_forward_decl


def main() -> None:
    # 可以先用 seed_0001 做 debug，后面你再改成批量跑
    seed = Path("data/seeds/clean/seed_0001.c")
    out = Path("results/debug/prune_struct_to_forward_decl/seed_0001.prune_struct_to_forward_decl.c")

    ok = prune_struct_to_forward_decl(str(seed), str(out))
    if ok:
        print(f"[结果] 已生成变异文件: {out}")
    else:
        print("[结果] 本次未能进行 P5 变异（可能没有合适的 struct/union 定义）")


if __name__ == "__main__":
    main()
