#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path

from edgm.mutators.insert_illegal_cast import insert_illegal_cast


def main():
    seed_dir = Path("data/seeds/clean")
    out_root = Path("results/debug/insert_illegal_cast")
    out_root.mkdir(parents=True, exist_ok=True)

    for seed in sorted(seed_dir.glob("*.c")):
        out_path = out_root / f"{seed.stem}.insert_illegal_cast.c"
        ok = insert_illegal_cast(str(seed), str(out_path))
        if ok:
            print(f"[结果] 已生成变异文件: {out_path}")
        else:
            print(f"[结果] {seed.name}: 未找到可用的表达式，未变异。")


if __name__ == "__main__":
    main()
