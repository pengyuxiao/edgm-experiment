#!/usr/bin/env python3
from pathlib import Path
import sys

# 让 Python 能找到 src/ 目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edgm.ast.ast_utils import list_functions

def main():
    seeds_clean = PROJECT_ROOT / "data" / "seeds" / "clean"
    c_files = sorted(seeds_clean.glob("*.c"))
    if not c_files:
        print(f"[错误] 在 {seeds_clean} 下没有找到任何 .c 文件，请确认已运行 01 和 02 步骤。")
        return

    # 先拿第一个 seed 来测试
    target = c_files[0]
    list_functions(target)

if __name__ == "__main__":
    main()
