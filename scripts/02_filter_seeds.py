#!/usr/bin/env python3
import os
import subprocess
import shutil
from pathlib import Path

"""
从 data/seeds/raw/ 读取所有 .c 文件，
分别用 gcc 和 clang 编译一次（只做语法检查），
只保留「两个编译器都没有 error」的文件到 data/seeds/clean/
"""

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "seeds" / "raw"
CLEAN_DIR = PROJECT_ROOT / "data" / "seeds" / "clean"

# 可以在这里修改为你想用的编译器命令
GCC = "gcc"
CLANG = "clang"

# 编译选项：只做语法检查，不生成目标文件
RUNTIME_DIR = "/home/pyx/edgm-experiment/src/csmithedge/csmith/runtime"

# 编译选项：加上 runtime 路径，只做语法检查
COMMON_FLAGS = ["-std=c11", f"-I{RUNTIME_DIR}", "-fsyntax-only"]

def compile_with(compiler: str, src: Path) -> bool:
    """用指定编译器编译一个源文件，只检查语法是否通过。
    返回 True 表示没有 error（warning 不影响），False 表示有 error 或崩溃。
    """
    cmd = [compiler] + COMMON_FLAGS + [str(src)]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        print(f"[错误] 找不到编译器 {compiler}，请检查是否已安装并在 PATH 中。")
        return False

    # 编译器返回码 != 0，说明有 error（或者严重 warning 被视为 error）
    if result.returncode != 0:
        # 你也可以临时打开下面两行看看具体报错
        # print(f"[{compiler}] 编译失败: {src.name}")
        # print(result.stderr)
        return False

    return True

def main():
    if not RAW_DIR.exists():
        print(f"[错误] 原始种子目录不存在: {RAW_DIR}")
        return

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)

    seeds = sorted(RAW_DIR.glob("*.c"))
    if not seeds:
        print(f"[提示] 在 {RAW_DIR} 下没有找到任何 .c 文件，请先运行 01_gen_seeds.sh")
        return

    total = len(seeds)
    kept = 0

    print(f"[信息] 共找到 {total} 个原始种子，开始筛选...")

    for i, src in enumerate(seeds, start=1):
        print(f"[{i}/{total}] 检查 {src.name} ...", end="")

        ok_gcc = compile_with(GCC, src)
        ok_clang = compile_with(CLANG, src)

        if ok_gcc and ok_clang:
            dest = CLEAN_DIR / src.name
            shutil.copy2(src, dest)
            kept += 1
            print(" 通过 ✓")
        else:
            print(" 丢弃 ✗")

    print(f"[结果] 总计 {total} 个种子，保留 {kept} 个到 {CLEAN_DIR}")

if __name__ == "__main__":
    main()
