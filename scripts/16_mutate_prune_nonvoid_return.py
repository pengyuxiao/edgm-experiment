#!/usr/bin/env python3
from pathlib import Path

from edgm.mutators.prune_nonvoid_return import prune_nonvoid_return


def main() -> None:
    # 先对 seed_0001 做 debug，后面你可以换成批量框架
    seed = Path("data/seeds/clean/seed_0001.c")
    out = Path("results/debug/prune_nonvoid_return/seed_0001.prune_nonvoid_return.c")

    ok = prune_nonvoid_return(str(seed), str(out))
    if ok:
        print(f"[结果] 已生成变异文件: {out}")
    else:
        print("[结果] 本次未能进行 P9 变异（可能没有合适的非 void 函数或 ReturnStmt）")


if __name__ == "__main__":
    main()
