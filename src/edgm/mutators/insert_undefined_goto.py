from __future__ import annotations

from pathlib import Path
from typing import List, Set

from clang.cindex import Cursor, CursorKind, SourceLocation

from edgm.ast.parse import parse_c_file
from edgm.ast.ast_utils import _is_from_main_file

import random

def _loc_to_offset(text: str, line: int, col: int) -> int:
    """
    把 (行, 列) 转成字符串 offset。
    libclang 是 1-based line/column，这里转成 Python 0-based index。
    """
    lines = text.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        raise ValueError(f"line 超出范围: {line}, 文件总行数: {len(lines)}")

    offset = 0
    for i in range(line - 1):
        offset += len(lines[i])
    offset += (col - 1)
    return offset


def _collect_compounds_and_names(
    root: Cursor, main_path: Path
) -> tuple[list[Cursor], Set[str], Set[str]]:
    """
    遍历整棵 AST：
      - 收集所有来自主文件、且处在函数体内的 CompoundStmt
      - 收集所有 label 名
      - 收集其它所有声明标识符名（函数/变量/typedef/struct tag/enum 等）
    """
    compounds: List[Cursor] = []
    ident_names: Set[str] = set()
    label_names: Set[str] = set()

    def visit(node: Cursor) -> None:
        # 一如既往：只把来自主文件的节点当“候选”，但会递归遍历其它文件的子节点
        if not _is_from_main_file(node, main_path):
            for ch in node.get_children():
                visit(ch)
            return

        kind = node.kind

        # label 语句
        if kind == CursorKind.LABEL_STMT and node.spelling:
            label_names.add(node.spelling)

        # 各种“有名字的声明”，用来做 goto label 的“迷惑基底”
        if node.spelling:
            if kind in (
                CursorKind.FUNCTION_DECL,
                CursorKind.VAR_DECL,
                CursorKind.PARM_DECL,
                CursorKind.STRUCT_DECL,
                CursorKind.UNION_DECL,
                CursorKind.ENUM_DECL,
                CursorKind.ENUM_CONSTANT_DECL,
                CursorKind.FIELD_DECL,
                CursorKind.TYPEDEF_DECL,
            ):
                ident_names.add(node.spelling)

        # CompoundStmt：只要在函数体内部
        if kind == CursorKind.COMPOUND_STMT:
            # 向上追父节点，看看是不是在某个 FunctionDecl 里
            p = node.semantic_parent
            in_func = False
            while p is not None:
                if p.kind == CursorKind.FUNCTION_DECL:
                    in_func = True
                    break
                p = p.semantic_parent
            if in_func:
                compounds.append(node)

        for ch in node.get_children():
            visit(ch)

    visit(root)
    return compounds, ident_names, label_names


def _choose_label_name(ident_names: Set[str], label_names: Set[str]) -> str:
    """
    根据已有标识符生成一个“看起来像某个真实符号”的 label 名，
    并确保目前没有同名 LabelStmt。
    """
    import random

    # 排除已经用作 label 的名字
    bases = [n for n in ident_names if n and n not in label_names]
    if not bases:
        bases = ["target", "retry", "again", "L"]

    # 尝试多次生成一个没被 label 用过的名字
    for _ in range(32):
        base = random.choice(bases)
        mode = random.randint(0, 3)
        if mode == 0:
            candidate = base
        elif mode == 1:
            candidate = base + "_retry"
        elif mode == 2:
            candidate = base + "2"
        else:
            candidate = base + "_L"

        if candidate not in label_names:
            return candidate

    # fallback：实在撞名就暴力加后缀
    return random.choice(bases) + "_bad_label"


def _get_offset_for_location(src_text: str, loc: SourceLocation) -> int:
    """
    将 SourceLocation 映射到 offset；优先用 loc.offset，没有的话退回 (line, col)。
    """
    # 某些 libclang 版本有 loc.offset 属性
    if hasattr(loc, "offset") and getattr(loc, "offset") is not None:
        return loc.offset  # type: ignore[attr-defined]

    return _loc_to_offset(src_text, loc.line, loc.column)


def _insert_goto_into_block(
    text: str, block: Cursor, label: str
) -> str:
    """
    在给定 CompoundStmt 的 '{' 后插入一条 goto label; 语句。
    """
    start = block.extent.start
    end = block.extent.end
    if not start.file or not end.file:
        return text

    start_off = _loc_to_offset(text, start.line, start.column)

    # 从 extent.start 开始向后找到第一个 '{'
    i = start_off
    while i < len(text) and text[i] != "{":
        i += 1
    if i >= len(text) or text[i] != "{":
        # 理论上不会发生，保守处理直接返回原文
        return text

    insert_pos = i + 1  # 在 '{' 后面插入

    # 这里缩进简单点：直接 4 空格。想更漂亮可以根据 start.column 来算 indent。
    indent = "    "
    insert_stmt = f"\n{indent}goto {label};\n"

    new_text = text[:insert_pos] + insert_stmt + text[insert_pos:]
    return new_text


def insert_undefined_goto(src: str, dst: str) -> bool:
    """
    I3: 在某个函数体内部的 CompoundStmt 里插入一条 goto 语句，
        其 label 名来自已有标识符（函数/变量/typedef/...），但整个 TU 中没有对应的 LabelStmt。
        预期触发：label 'xxx' used but not defined / use of undeclared label 'xxx'。

    返回值：
      - True: 成功插入了一条 goto
      - False: 没有找到合适的 CompoundStmt 或可用的标识符，未变异
    """
    src_path = Path(src).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    tu = parse_c_file(src_path)
    root = getattr(tu, "cursor", tu)

    compounds, ident_names, label_names = _collect_compounds_and_names(
        root, src_path
    )

    if not compounds:
        return False

    if not ident_names:
        # 实在没有可用标识符也可以 fallback 到一些固定名字
        ident_names = {"target", "retry", "again", "L"}

    label = _choose_label_name(ident_names, label_names)

    # 这里可以随机选一个 block，也可以选第一个。为了可复现性，先用“第一个”。
    target_block = random.choice(compounds)

    text = src_path.read_text(encoding="utf-8")
    new_text = _insert_goto_into_block(text, target_block, label)

    dst_path = Path(dst).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(new_text, encoding="utf-8")

    return True
