#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path

from edgm.mutators.insert_illegal_case_default import insert_illegal_case_default


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = PROJECT_ROOT / "data" / "seeds" / "clean"
OUT_DIR = PROJECT_ROOT / "results" / "debug" / "insert_illegal_case_default"


def main() -> None:
    seed = SEED_DIR / "seed_0001.c"
    out = OUT_DIR / "seed_0001.insert_illegal_case_default.c"

    ok = insert_illegal_case_default(str(seed), str(out))
    if not ok:
        print("[结果] 本次没有找到合适的 CompoundStmt/标识符，未进行 case/default 变异。")
        return

    print(f"[结果] 已生成变异文件: {out}")


if __name__ == "__main__":
    main()
