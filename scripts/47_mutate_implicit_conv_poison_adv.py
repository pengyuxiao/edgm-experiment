#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

# Ensure repo root is on sys.path so "import src...." works
REPO_ROOT = Path(__file__).resolve().parents[1]  # scripts/.. == repo root
sys.path.insert(0, str(REPO_ROOT))

from src.edgm.mutators.implicit_conv_poison_adv import mutate  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/47_mutate_implicit_conv_poison_adv.py <in.c> <out.c> [seed]")
        sys.exit(2)

    in_path = sys.argv[1]
    out_path = sys.argv[2]
    seed = int(sys.argv[3]) if len(sys.argv) >= 4 else None
    mutate(in_path, out_path, seed=seed)


if __name__ == "__main__":
    main()
