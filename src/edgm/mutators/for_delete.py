from __future__ import annotations

from pathlib import Path
from typing import Optional

from clang.cindex import Cursor, CursorKind

from edgm.ast.ast_utils import parse_c_file, _is_from_main_file



def _find_first_for_node(path: str) -> Optional[Cursor]:
    """
    在给定的 C 源文件里找到第一个 FOR_STMT 对应的 Cursor。
    只考虑来自主源文件本身的节点。

    这里兼容两种情况：
      - parse_c_file 返回 TranslationUnit，此时 root = tu.cursor
      - parse_c_file 返回 Cursor（根游标），此时 root = tu
    """
    src_path = Path(path).resolve()
    tu = parse_c_file(src_path)

    # 🔧 关键修复：同时兼容 tu 和 tu.cursor，避免 AttributeError
    root = getattr(tu, "cursor", tu)

    target: Optional[Cursor] = None

    def visit(node: Cursor):
        nonlocal target
        if target is not None:
            return

        # 展开别的文件的子节点，但不把它们算作“来自主文件”
        if not _is_from_main_file(node, src_path):
            for child in node.get_children():
                visit(child)
            return

        if node.kind == CursorKind.FOR_STMT:
            target = node
            return

        for child in node.get_children():
            visit(child)

    visit(root)
    return target


def _delete_node_from_source(src_code: str, node: Cursor) -> str:
    """
    根据 node.extent 把对应源码片段删掉，返回新的源码字符串。
    """
    extent = node.extent
    start = extent.start
    end = extent.end

    # 保险起见，检查一下文件是否存在
    if start.file is None or end.file is None:
        raise ValueError("node extent has no file information")

    lines = src_code.splitlines(keepends=True)

    # Clang 的行列从 1 开始，这里转成 Python 下标（0 开始）
    s_line = start.line - 1
    s_col = start.column - 1
    e_line = end.line - 1
    e_col = end.column - 1

    if s_line == e_line:
        # 同一行上，把 [s_col, e_col) 这一段删掉
        lines[s_line] = lines[s_line][:s_col] + lines[e_line][e_col:]
    else:
        # 多行：保留开始行之前的部分和结束行之后的部分，中间全清空
        lines[s_line] = lines[s_line][:s_col]
        lines[e_line] = lines[e_line][e_col:]
        for i in range(s_line + 1, e_line):
            lines[i] = ""

    return "".join(lines)


def delete_first_for(src_path: str, dst_path: str) -> bool:
    """
    在 src_path 对应的 C 程序里删除第一个 for 语句，并把结果写到 dst_path。
    如果找不到 for 语句，返回 False；否则返回 True。
    """
    src_path = str(src_path)
    dst_path = str(dst_path)

    node = _find_first_for_node(src_path)
    if node is None:
        return False

    with open(src_path, "r", encoding="utf-8") as f:
        src_code = f.read()

    new_code = _delete_node_from_source(src_code, node)

    out_path = Path(dst_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(new_code)

    return True
