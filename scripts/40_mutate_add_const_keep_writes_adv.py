# -*- coding: utf-8 -*-
import argparse
from edgm.mutators.add_const_keep_writes_adv import insert_add_const_keep_writes_adv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("in_file")
    ap.add_argument("out_file")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    ok = insert_add_const_keep_writes_adv(args.in_file, args.out_file, seed=args.seed)
    if ok:
        print(f"[结果] 已生成 C-const-adv 变异文件: {args.out_file}")
    else:
        print(f"[结果] C-const-adv 未插入（已原样拷贝或跳过）: {args.out_file}")


if __name__ == "__main__":
    main()
