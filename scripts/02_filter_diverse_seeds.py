#!/usr/bin/env python3
import argparse
import subprocess
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 默认使用当前 PATH 下的 gcc/clang
GCC = "gcc"
CLANG = "clang"

RUNTIME_DIR = "/home/pyx/edgm-experiment/src/csmithedge/csmith/runtime"
COMMON_FLAGS = ["-std=c11", f"-I{RUNTIME_DIR}", "-fsyntax-only"]

def compile_with(compiler: str, src: Path) -> bool:
    cmd = [compiler] + COMMON_FLAGS + [str(src)]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        print(f"[错误] 找不到编译器 {compiler}，请检查 PATH。")
        return False
    return r.returncode == 0

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", default=str(PROJECT_ROOT / "data/seeds/diverse/diversify_syntax"))
    ap.add_argument("--out-dir", default=str(PROJECT_ROOT / "data/seeds/diverse/clean_diversify_syntax"))
    ap.add_argument("--glob", default="*.c")
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)

    if not in_dir.exists():
        print(f"[错误] 输入目录不存在: {in_dir}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)

    seeds = sorted(in_dir.glob(args.glob))
    if not seeds:
        print(f"[提示] 输入目录下没有匹配文件: {in_dir}/{args.glob}")
        return 0

    total = len(seeds)
    kept = 0
    bad = 0

    print(f"[信息] 从 {in_dir} 读取 {total} 个文件，筛选到 {out_dir}")
    for i, src in enumerate(seeds, start=1):
        print(f"[{i:>3}/{total}] {src.name} ...", end="")
        ok_gcc = compile_with(GCC, src)
        ok_clang = compile_with(CLANG, src)

        if ok_gcc and ok_clang:
            shutil.copy2(src, out_dir / src.name)
            kept += 1
            print(" 通过 ✓")
        else:
            bad += 1
            print(" 丢弃 ✗")

    print(f"[结果] 输入 {total}，保留 {kept}，丢弃 {bad}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
