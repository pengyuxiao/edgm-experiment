# scripts/45_mutate_for_init_inject_bad_expr_advppp.py
from pathlib import Path  # 以后按这个写：先 import Path

import sys


def _add_repo_root():
    # 把仓库根目录加进 sys.path，避免 "No module named src"
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))


_add_repo_root()

from src.edgm.mutators.for_init_inject_bad_expr_adv import mutate  # noqa: E402


def main():
    if len(sys.argv) != 3:
        print("usage: python3 scripts/45_mutate_for_init_inject_bad_expr_advppp.py <in.c> <out.c>")
        raise SystemExit(2)

    in_c, out_c = sys.argv[1], sys.argv[2]
    Path(out_c).parent.mkdir(parents=True, exist_ok=True)

    mutate(in_c, out_c, seed=None)


if __name__ == "__main__":
    main()
