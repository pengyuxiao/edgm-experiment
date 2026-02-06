#!/usr/bin/env python3
from pathlib import Path

from clang.cindex import Index  # 只需要 Index，不再从这里导 Cursor

# Csmith runtime 路径（用于让 clang 找到 csmith.h）
RUNTIME_DIR = "/home/pyx/edgm-experiment/src/csmithedge/csmith/runtime"


def parse_c_file(path: Path):
    """
    使用 libclang 解析一个 C 源文件，返回 TranslationUnit 对象（不是 cursor！）
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"源文件不存在: {path}")

    index = Index.create()

    args = [
        "-std=c11",
        f"-I{RUNTIME_DIR}",
    ]

    tu = index.parse(str(path), args=args)
    if not tu:
        raise RuntimeError(f"解析失败: {path}")

    # 打印诊断信息（只打印，不阻止继续）
    for diag in tu.diagnostics:
        print(f"[AST diag] {diag}")

    # ⭐ 关键：返回 TranslationUnit，本身带 .cursor 属性
    return tu
