#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
SRC_DIR = os.path.join(ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from edgm.mutators.diversify_syntax import diversify_syntax, DiversifyConfig


def main():
    ap = argparse.ArgumentParser(description="EDGM diversify-only mutator (no compile errors expected)")
    ap.add_argument("in_file")
    ap.add_argument("out_file")
    ap.add_argument("--seed", type=int, default=None)

    # old knobs (prob mode)
    ap.add_argument("--p-global-typedef", type=float, default=0.55)
    ap.add_argument("--p-global-enum", type=float, default=0.45)
    ap.add_argument("--func-min", type=int, default=0)
    ap.add_argument("--func-max", type=int, default=6)

    # NEW: force to include ALL syntax constructs at least once
    ap.add_argument("--force-all", action="store_true",
                    help="force inject global typedef/enum + local typedef/enum + switch + while + do/while at least once")

    args = ap.parse_args()

    cfg = DiversifyConfig(
        p_global_typedef=args.p_global_typedef,
        p_global_enum=args.p_global_enum,
        func_inserts_min=args.func_min,
        func_inserts_max=args.func_max,
        force_all=args.force_all,
    )

    ok = diversify_syntax(args.in_file, args.out_file, seed=args.seed, config=cfg)
    if ok:
        print(f"[结果] 已生成 diversify 变异文件: {args.out_file}")
        return 0
    print("[debug] diversify: failed/skip")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
