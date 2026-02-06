#!/usr/bin/env python3
from pathlib import Path

from clang.cindex import Index, CursorKind



# 如果 libclang 找不到，可以手动指定 libclang.so 路径
# 比如：Config.set_library_file("/usr/lib/llvm-14/lib/libclang-14.so")
# 先不写死，如果报错再来改这里

# Csmith runtime 路径（用于让 clang 找到 csmith.h）
RUNTIME_DIR = "/home/pyx/edgm-experiment/src/csmithedge/csmith/runtime"

def parse_c_file(path: Path):
    """使用 libclang 解析一个 C 源文件，返回 AST 根节点。"""
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

    # 如果有编译错误，打印一下诊断信息（这里只是警告，不停止）
    for diag in tu.diagnostics:
        print(f"[AST diag] {diag}")

    return tu

def list_functions(path: Path):
    """打印文件中的所有函数定义名称和位置。"""
    tu = parse_c_file(path)
    root = getattr(tu, "cursor", tu)

    print(f"解析文件: {path}")
    print("函数定义列表:")
    for child in root.get_children():
        if child.kind == CursorKind.FUNCTION_DECL:
            loc = child.location
            print(f"  - {child.spelling}  (line {loc.line}, column {loc.column})")


from typing import Dict, List


def _is_from_main_file(node, main_path: Path) -> bool:
    """
    只保留来自种子源文件本身的节点，过滤掉头文件 / runtime 里的定义。
    """
    loc = node.location
    if loc is None or loc.file is None:
        return False

    # libclang 这里有时给绝对路径，有时只给文件名，用两层判断
    file_name = Path(loc.file.name).name
    main_name = main_path.name
    if file_name != main_name:
        return False

    return True


def collect_interesting_nodes(path: str) -> Dict[str, List]:
    """
    收集后续变异可能用到的 AST 节点：
      - 用户函数定义
      - for / while / if 语句
      - 函数调用
      - 变量声明
    只统计来自 seed 源文件本身的节点。
    """
    src_path = Path(path).resolve()
    tu = parse_c_file(src_path)

    buckets: Dict[str, List] = {
        "functions": [],
        "for": [],
        "while": [],
        "if": [],
        "calls": [],
        "vardecls": [],
    }

    def visit(node):
        # 只看来自当前 seed 源文件的节点
        if not _is_from_main_file(node, src_path):
            # 但还是要递归子节点，因为有些结构可能挂在 translation unit 下
            for child in node.get_children():
                visit(child)
            return

        kind = node.kind

        if kind == CursorKind.FUNCTION_DECL:
            buckets["functions"].append(node)
        elif kind == CursorKind.FOR_STMT:
            buckets["for"].append(node)
        elif kind == CursorKind.WHILE_STMT:
            buckets["while"].append(node)
        elif kind == CursorKind.IF_STMT:
            buckets["if"].append(node)
        elif kind == CursorKind.CALL_EXPR:
            buckets["calls"].append(node)
        elif kind == CursorKind.VAR_DECL:
            buckets["vardecls"].append(node)

        for child in node.get_children():
            visit(child)

    root = getattr(tu, "cursor", tu)
    visit(root)
    return buckets
