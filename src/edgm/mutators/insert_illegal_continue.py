from __future__ import annotations

from pathlib import Path
from typing import List
import random

from clang.cindex import Cursor, CursorKind

from edgm.ast.parse import parse_c_file
from edgm.ast.ast_utils import _is_from_main_file


def _loc_to_offset(text: str, line: int, col: int) -> int:
    """
    把 (行, 列) 转成字符串 offset。
    libclang 给的是 1-based line/column，我们要转成 Python 字符串的 0-based index。
    """
    lines = text.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        raise ValueError(f"line 超出范围: {line}, 文件总行数: {len(lines)}")

    offset = 0
    for i in range(line - 1):
        offset += len(lines[i])
    offset += (col - 1)
    return offset


def _collect_compound_targets(src_path: Path) -> List[Cursor]:
    """
    收集“可以插入非法 continue;”的 CompoundStmt：

      - 必须来自主源文件
      - 不能位于任何 loop/switch 之内（即所有祖先节点中都没有 for/while/do/switch）
        这样在这个块里插入 continue; 就会触发：
          - continue statement not within a loop
    """
    tu = parse_c_file(src_path)
    root = getattr(tu, "cursor", tu)

    candidates: List[Cursor] = []

    LOOP_OR_SWITCH = {
        CursorKind.FOR_STMT,
        CursorKind.WHILE_STMT,
        CursorKind.DO_STMT,
        CursorKind.SWITCH_STMT,
    }

    def visit(node: Cursor, in_loop_or_switch: bool) -> None:
        current_in = in_loop_or_switch or (node.kind in LOOP_OR_SWITCH)

        if _is_from_main_file(node, src_path):
            if node.kind == CursorKind.COMPOUND_STMT and not current_in:
                # 一个“普通块”：不在 loop / switch 里，可以作为插入 continue; 的目标
                candidates.append(node)

            for ch in node.get_children():
                visit(ch, current_in)
        else:
            for ch in node.get_children():
                visit(ch, current_in)

    visit(root, False)
    return candidates


def _insert_continue_in_text(text: str, compound: Cursor) -> str | None:
    """
    在给定 CompoundStmt 对应的源码块里，在 '{' 之后插入一条独立的 'continue;' 语句。

    返回新的源码文本；如果失败则返回 None。
    """
    start = compound.extent.start
    if start.file is None:
        return None

    try:
        start_off = _loc_to_offset(text, start.line, start.column)
    except ValueError:
        return None

    # 从起始处向后找到第一个 '{'
    brace_pos = text.find("{", start_off)
    if brace_pos == -1:
        return None

    insert_pos = brace_pos + 1  # 在 '{' 之后插入

    # 计算当前这一行的缩进（空格 / 制表符前缀）
    line_start = text.rfind("\n", 0, brace_pos)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1

    indent_chars = []
    while (
        line_start + len(indent_chars) < len(text)
        and text[line_start + len(indent_chars)] in (" ", "\t")
    ):
        indent_chars.append(text[line_start + len(indent_chars)])
    indent = "".join(indent_chars)

    # 插入一行：<换行> + 原缩进 + 4 空格 + continue; + <换行>
    insert_code = "\n" + indent + "    continue;\n"

    new_text = text[:insert_pos] + insert_code + text[insert_pos:]
    return new_text


def insert_illegal_continue(src: str, dst: str) -> bool:
    """
    I2 变异：在“非循环 / 非 switch 的普通块”里插入一条 continue; 语句。

    - 不改变原有语句，只是往块里多塞一条 `continue;`
    - 位置保证不在任何 loop / switch 内
      => 编译器应该报：continue statement not within a loop

    返回：
      True  表示成功插入了一条非法 continue;
      False 表示没有找到合适的块，或者插入失败
    """
    src_path = Path(src).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    text = src_path.read_text(encoding="utf-8")

    compounds = _collect_compound_targets(src_path)
    if not compounds:
        return False

    target = random.choice(compounds)

    new_text = _insert_continue_in_text(text, target)
    if new_text is None or new_text == text:
        return False

    dst_path = Path(dst).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(new_text, encoding="utf-8")

    return True
