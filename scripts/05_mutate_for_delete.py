#!/usr/bin/env python3
import sys
import argparse
from pathlib import Path

# 把 src/ 加到 sys.path，才能 import edgm.*
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edgm.mutators.for_delete import delete_first_for


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="05_mutate_for_delete.py",
        description="Mutator: delete the first 'for' loop (for_delete). Requires explicit --in/--out."
    )
    ap.add_argument(
        "--in", dest="in_file", required=True,
        help="Input parent C file path (e.g., data/corpus/pool/seed_0001.div.c)"
    )
    ap.add_argument(
        "--out", dest="out_file", required=True,
        help="Output mutant C file path (e.g., data/mutants/queue/seed_0001.for_deleted.c)"
    )
    ap.add_argument(
        "--mkdir", action="store_true",
        help="Create parent directory of --out if not exists"
    )
    return ap


def main(argv=None) -> int:
    ap = build_argparser()
    args = ap.parse_args(argv)

    src = Path(args.in_file)
    dst = Path(args.out_file)

    if not src.exists():
        print(f"[错误] input not found: {src}", file=sys.stderr)
        return 2

    if args.mkdir:
        dst.parent.mkdir(parents=True, exist_ok=True)

    ok = delete_first_for(str(src), str(dst))
    if not ok:
        print(f"[结果] {src} 里面没有 for 循环，未生成变异文件")
        return 1

    print(f"[结果] 已生成变异文件: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
