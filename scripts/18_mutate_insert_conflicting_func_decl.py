# scripts/18_mutate_insert_conflicting_func_decl.py

from pathlib import Path

from edgm.mutators.insert_conflicting_func_decl import insert_conflicting_func_decl


if __name__ == "__main__":
    PROJECT_ROOT = Path(__file__).resolve().parents[1]

    seed = PROJECT_ROOT / "data" / "seeds" / "clean" / "seed_0001.c"
    out_dir = PROJECT_ROOT / "results" / "debug" / "insert_conflicting_func_decl"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "seed_0001.insert_conflicting_func_decl.c"

    ok = insert_conflicting_func_decl(str(seed), str(out_path))
    if ok:
        print(f"[结果] 已生成变异文件: {out_path}")
    else:
        print("[结果] 未找到可用的函数定义，未变异。")
