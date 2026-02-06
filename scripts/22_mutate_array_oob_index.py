#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edgm.mutators.array_oob_index import mutate_array_oob_index


def build_argparser():
    ap = argparse.ArgumentParser(
        prog="22_mutate_array_oob_index.py",
        description="Mutator: array out-of-bounds index (array_oob_index)."
    )
    ap.add_argument("--in", dest="in_file", required=True, help="Input parent C file")
    ap.add_argument("--out", dest="out_file", required=True, help="Output mutant C file")
    ap.add_argument("--mkdir", action="store_true", help="Create parent directory of --out")
    return ap


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    src = Path(args.in_file)
    dst = Path(args.out_file)

    if not src.exists():
        print(f"[错误] input not found: {src}", file=sys.stderr)
        return 2

    if args.mkdir:
        dst.parent.mkdir(parents=True, exist_ok=True)

    ok = mutate_array_oob_index(str(src), str(dst))
    if not ok:
        print("[结果] 未找到可用的数组下标位置，未变异。")
        return 1

    print(f"[结果] 已生成变异文件: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
