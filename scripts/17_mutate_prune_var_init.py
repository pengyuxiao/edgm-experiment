#!/usr/bin/env python3
# scripts/17_mutate_prune_var_init.py

from pathlib import Path

from edgm.mutators.prune_var_init import prune_var_init


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    seed = project_root / "data" / "seeds" / "clean" / "seed_0001.c"

    out_dir = project_root / "results" / "debug" / "prune_var_init"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "seed_0001.prune_var_init.c"

    ok = prune_var_init(str(seed), str(out_path))
    if ok:
        print(f"[结果] 已生成变异文件: {out_path}")
    else:
        print("[结果] 未找到合适的局部变量进行初始化删除，未变异。")


if __name__ == "__main__":
    main()
