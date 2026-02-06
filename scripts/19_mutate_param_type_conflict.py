# scripts/19_mutate_param_type_conflict.py
from pathlib import Path

from edgm.mutators.insert_param_type_conflict import insert_param_type_conflict

if __name__ == "__main__":
    # 先写成只对 seed_0001.c 做 debug，跟之前一样风格
    seed = Path("data/seeds/clean/seed_0001.c")
    out_dir = Path("results/debug/param_type_conflict")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{seed.stem}.param_type_conflict.c"
    ok = insert_param_type_conflict(str(seed), str(out_path))

    if not ok:
        print("[结果] 未找到可用的形参进行类型冲突变异，未变异。")
