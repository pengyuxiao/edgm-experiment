# src/edgm/mutators/prune_struct_to_forward_decl.py
#
# P5: 将某个 struct/union 的完整定义替换为仅前向声明：
#     struct S { ... };  =>  struct S;
#     union U { ... };   =>  union U;
#
# 期望产生：incomplete type 使用相关的诊断。

from __future__ import annotations

from pathlib import Path
from typing import Optional

from clang import cindex

import random

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSMITH_RUNTIME_DIR = PROJECT_ROOT / "src" / "csmithedge" / "csmith" / "runtime"


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _location_to_offset(loc: cindex.SourceLocation, text: str) -> int:
    """
    将 (line, column) 转成源码里的 offset。
    line/column 从 1 开始，offset 从 0 开始。
    """
    line = loc.line - 1
    col = loc.column - 1
    if line < 0:
        return 0

    lines = text.splitlines(keepends=True)
    if line >= len(lines):
        return len(text)

    return sum(len(l) for l in lines[:line]) + min(col, len(lines[line]))


def _is_record_definition_target(node: cindex.Cursor) -> bool:
    """
    选定可变异的 struct/union 定义：
    - kind 是 STRUCT_DECL 或 UNION_DECL
    - is_definition() 为 True
    - 有名字（spelling 非空）
    - 父节点不是 TYPEDEF_DECL（先跳过 typedef struct {...} T 这种，留着以后高级玩法）
    """
    if node.kind not in (cindex.CursorKind.STRUCT_DECL, cindex.CursorKind.UNION_DECL):
        return False

    if not node.is_definition():
        return False

    if not node.spelling:
        # 匿名 struct/union，跳过
        return False

    parent = node.semantic_parent or node.lexical_parent
    if parent is not None and parent.kind == cindex.CursorKind.TYPEDEF_DECL:
        # typedef struct S { ... } S; 这种我们暂时不动
        return False

    return True


def _pick_struct_or_union_def(tu: cindex.TranslationUnit) -> Optional[cindex.Cursor]:
    """
    从 TU 中挑一个 struct/union 定义出来。
    这里先简单随机/第一个即可，后面如果想更加“高级”可以加筛选条件：
    - 要求至少有成员；
    - 要求有非指针变量使用等。
    """
    candidates = []

    def visit(node: cindex.Cursor) -> None:
        if _is_record_definition_target(node):
            candidates.append(node)
        for child in node.get_children():
            visit(child)

    visit(tu.cursor)

    if not candidates:
        return None

    # 这里先取第一个，想随机可以改成 random.choice(candidates)
    return random.choice(candidates)


def prune_struct_to_forward_decl(input_path: str, output_path: str) -> bool:
    """
    P5 核心接口：
    - 读取 input_path
    - 找到一个 struct/union 定义
    - 用前向声明替换
    - 写入 output_path
    - 返回是否成功进行了变异
    """
    in_path = Path(input_path)
    out_path = Path(output_path)

    src = _read_file(in_path)

    index = cindex.Index.create()
    args = [f"-I{CSMITH_RUNTIME_DIR}"]
    tu = index.parse(str(in_path), args=args)

    target = _pick_struct_or_union_def(tu)
    if target is None:
        return False

    kind = target.kind
    name = target.spelling

    if kind == cindex.CursorKind.STRUCT_DECL:
        kw = "struct"
    else:
        kw = "union"

    # 计算替换范围：使用 extent.start ~ extent.end
    start = target.extent.start
    end = target.extent.end

    start_off = _location_to_offset(start, src)
    end_off = _location_to_offset(end, src)

    # 构造前向声明文本
    # 保守一点后面加一个换行，避免和后面的代码黏在一行
    replacement = f"{kw} {name};\n"

    mutated = src[:start_off] + replacement + src[end_off:]

    _write_file(out_path, mutated)
    return True
