#!/usr/bin/env python3
import argparse
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", default="data/seeds/diverse/clean_diversify_syntax")
    ap.add_argument("--cursor", default="data/run_cursor.txt")
    args = ap.parse_args()

    seed_dir = Path(args.seed_dir)
    cur_path = Path(args.cursor)
    seed_dir = seed_dir.resolve()

    seeds = sorted(seed_dir.glob("*.c"))
    if not seeds:
        raise SystemExit(f"[ERR] no seeds under {seed_dir}")

    idx = 0
    if cur_path.exists():
        try:
            idx = int(cur_path.read_text().strip() or "0")
        except:
            idx = 0

    if idx >= len(seeds):
        idx = 0  # 循环（不让你跑到末尾就停）

    chosen = seeds[idx]
    cur_path.parent.mkdir(parents=True, exist_ok=True)
    cur_path.write_text(str(idx + 1), encoding="utf-8")

    print(str(chosen))

if __name__ == "__main__":
    main()
