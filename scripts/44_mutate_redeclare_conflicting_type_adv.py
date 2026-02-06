# -*- coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.edgm.mutators.redeclare_conflicting_type_adv import mutate


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: python3 scripts/44_mutate_redeclare_conflicting_type_adv.py <in.c> <out.c>")
        return 2

    in_path, out_path = argv[1], argv[2]
    ok = mutate(in_path, out_path, seed=None)
    if ok:
        print(f"[结果] 已生成 C-redeclare-conflict-adv 变异文件: {out_path}")
        return 0
    else:
        print(f"[结果] C-redeclare-conflict-adv 未插入（已原样拷贝或跳过）: {out_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
