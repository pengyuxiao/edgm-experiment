# scripts/31_mutate_insert_nonconst_global_init_adv.py

import argparse
import sys
from pathlib import Path

# 确保 scripts 直接运行时也能 import src/edgm
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edgm.mutators.insert_nonconst_global_init import insert_nonconst_global_init_adv


def main():
    ap = argparse.ArgumentParser(description="#31 / I14-adv: non-constant global initializer (obfuscated)")
    ap.add_argument("in_file")
    ap.add_argument("out_file")
    args = ap.parse_args()

    ok = insert_nonconst_global_init_adv(args.in_file, args.out_file)
    if ok:
        print(f"[结果] 已生成变异文件: {args.out_file}")
    else:
        print("[结果] 未找到可变异的全局初始化点，未变异。")


if __name__ == "__main__":
    main()
