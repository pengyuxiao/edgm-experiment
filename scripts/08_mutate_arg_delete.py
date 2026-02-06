#!/usr/bin/env python3
from pathlib import Path

from edgm.mutators.arg_delete import delete_call_arg

# 和前几个脚本保持风格一致
SEED_DIR = Path("data/seeds/clean")
OUT_DIR = Path("results/debug/arg_delete")


def main():
    seed = SEED_DIR / "seed_0001.c"    # 先对 seed_0001 做 debug
    out = OUT_DIR / "seed_0001.arg_deleted.c"

    ok = delete_call_arg(str(seed), str(out))

    if ok:
        print(f"[结果] 已生成变异文件: {out}")
    else:
        print("[结果] 未找到合适的调用表达式，未进行变异")


if __name__ == "__main__":
    main()
