# src/edgm/mutators/insert_illegal_case_default.py
# I4: 在非 switch 的 CompoundStmt 中插入非法 case/default

from __future__ import annotations

import random
from pathlib import Path
from typing import List, Optional, Tuple

from clang import cindex


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSMITH_RUNTIME_DIR = PROJECT_ROOT / "src" / "csmithedge" / "csmith" / "runtime"


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _get_parent(cur: cindex.Cursor) -> Optional[cindex.Cursor]:
    """优先用 semantic_parent，退而求其次用 lexical_parent。注意避免和 None 比较。"""
    parent = cur.semantic_parent
    if parent is None:
        parent = cur.lexical_parent

    if parent is None:
        return None

    # 避免 clang_equalCursors(None, Cursor) 之类的调用
    try:
        if parent == cur:
            return None
    except TypeError:
        return None

    return parent


def _is_inside_switch(cursor: cindex.Cursor) -> bool:
    """向上爬 AST，看是否被某个 SwitchStmt 包住。"""
    cur: Optional[cindex.Cursor] = cursor
    while cur is not None and cur.kind != cindex.CursorKind.TRANSLATION_UNIT:
        if cur.kind == cindex.CursorKind.SWITCH_STMT:
            return True
        cur = _get_parent(cur)
    return False


def _collect_identifier_candidates(tu: cindex.TranslationUnit) -> List[str]:
    """
    收集可用作 case 表达式的标识符：
    - 优先枚举常量枚举项名字
    - 然后再考虑变量 / 形参 / 函数名
    """
    enums: List[str] = []
    others: List[str] = []

    def visit(node: cindex.Cursor) -> None:
        kind = node.kind
        spelling = node.spelling

        if spelling:
            if kind == cindex.CursorKind.ENUM_CONSTANT_DECL:
                enums.append(spelling)
            elif kind in (
                cindex.CursorKind.VAR_DECL,
                cindex.CursorKind.PARM_DECL,
                cindex.CursorKind.FUNCTION_DECL,
            ):
                others.append(spelling)

        for child in node.get_children():
            visit(child)

    visit(tu.cursor)

    if enums:
        return enums
    return others


def _location_to_offset(loc: cindex.SourceLocation, text: str) -> int:
    """将 (line, column) 转成文件内 offset（基于原始源码字符串）"""
    line = loc.line - 1
    col = loc.column - 1
    if line < 0:
        return 0

    lines = text.splitlines(keepends=True)
    if line >= len(lines):
        return len(text)

    return sum(len(l) for l in lines[:line]) + min(col, len(lines[line]))


def _pick_compound_not_in_switch(
    tu: cindex.TranslationUnit,
) -> Optional[cindex.Cursor]:
    """
    挑选一个不在 switch 里的 CompoundStmt 作为插入目标。
    """
    candidates: List[cindex.Cursor] = []

    def visit(node: cindex.Cursor) -> None:
        if node.kind == cindex.CursorKind.COMPOUND_STMT:
            if not _is_inside_switch(node):
                candidates.append(node)
        for child in node.get_children():
            visit(child)

    visit(tu.cursor)

    if not candidates:
        return None

    return random.choice(candidates)


def _compute_insert_offset_for_compound(
    compound: cindex.Cursor, source: str
) -> Tuple[int, str]:
    """
    以 CompoundStmt 的起始位置为基准，在 '{' 之后插入一行。
    返回 (插入 offset, 当前缩进前缀)。
    """
    start = compound.extent.start
    start_off = _location_to_offset(start, source)

    # 从起始 offset 往后找第一个 '{'
    brace_pos = source.find("{", start_off)
    if brace_pos == -1:
        # 极端情况：没找到 '{'，那就直接用 start_off，给个默认缩进
        return start_off, "    "

    # 找到 '{' 这一行的缩进：从行首到 '{' 前的空白
    line_start = source.rfind("\n", 0, brace_pos)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1

    indent = ""
    i = line_start
    while i < brace_pos and source[i] in (" ", "\t"):
        indent += source[i]
        i += 1

    insert_off = brace_pos + 1  # 在 '{' 之后插入
    return insert_off, indent + "    "  # 再加一个缩进层


def insert_illegal_case_default(input_path: str, output_path: str) -> bool:
    """
    I4 变异：在非 switch 的 CompoundStmt 中插入非法 case/default。
    返回是否成功进行了变异。
    """
    in_path = Path(input_path)
    out_path = Path(output_path)

    src = _read_file(in_path)

    index = cindex.Index.create()
    args = [f"-I{CSMITH_RUNTIME_DIR}"]
    tu = index.parse(str(in_path), args=args)

    # 1. 选一个非 switch 内的 CompoundStmt
    target_compound = _pick_compound_not_in_switch(tu)
    if target_compound is None:
        return False

    # 2. 选一个 case 表达式用的标识符
    idents = _collect_identifier_candidates(tu)
    if not idents:
        label_expr = "0"
    else:
        label_expr = random.choice(idents)

    # 3. 决定是 case 还是 default
    use_default = random.choice([True, False])
    if use_default:
        label_line = "default:\n"
    else:
        label_line = f"case {label_expr}:\n"

    # 4. 计算插入位置 & 缩进
    insert_off, indent = _compute_insert_offset_for_compound(target_compound, src)

    insert_text = "\n" + indent + label_line

    mutated = src[:insert_off] + insert_text + src[insert_off:]

    _write_file(out_path, mutated)
    return True
