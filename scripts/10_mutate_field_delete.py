from __future__ import annotations

from pathlib import Path

from edgm.mutators.field_delete import delete_struct_field


def main() -> None:
    # 项目根目录：.../edgm-experiment
    project_root = Path(__file__).resolve().parents[1]

    seed = project_root / "data/seeds/clean/seed_0001.c"
    out_dir = project_root / "results/debug/field_delete"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "seed_0001.field_deleted.c"

    ok = delete_struct_field(str(seed), str(out))
    if ok:
        print(f"[结果] 已生成变异文件: {out}")
    else:
        # 没有找到“被使用过的字段”，不进行变异
        print("[结果] 本次未进行变异（没有找到使用过的 struct/union 字段）")


if __name__ == "__main__":
    main()
