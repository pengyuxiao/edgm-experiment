from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from clang.cindex import Cursor, CursorKind

# 统一走 ast_utils 这一套：parse_c_file 返回的是 TranslationUnit（有 .cursor）
from edgm.ast.ast_utils import parse_c_file, _is_from_main_file


def _is_local_vardecl(node: Cursor) -> bool:
    """
    判断一个 VAR_DECL 是否是局部变量声明（优先删这种）。
    局部变量的一般语义父节点是 FUNCTION_DECL 或 COMPOUND_STMT。
    """
    if node.kind != CursorKind.VAR_DECL:
        return False
    parent = node.semantic_parent
    if parent is None:
        return False
    return parent.kind in (CursorKind.FUNCTION_DECL, CursorKind.COMPOUND_STMT)


def _collect_vardecls(root: Cursor, main_path: Path) -> List[Cursor]:
    """
    在整棵 AST 里收集所有来自主文件的 VAR_DECL。
    """
    result: List[Cursor] = []

    def visit(node: Cursor):
        # 只把“来自主源文件”的节点记到 result，但子节点要照样递归
        if not _is_from_main_file(node, main_path):
            for child in node.get_children():
                visit(child)
            return

        if node.kind == CursorKind.VAR_DECL:
            result.append(node)

        for child in node.get_children():
            visit(child)

    visit(root)
    return result


def _is_used_var(root: Cursor, var_decl: Cursor) -> bool:
    """
    判断这个变量声明在后面是否被真实用到：
      - 查找所有 DECL_REF_EXPR
      - 如果它的 .referenced == var_decl，则认为被使用
    为了稍微高效一点：
      - 如果是局部变量，只在它所在函数体里查
      - 否则在整棵 AST 上查
    """
    # 决定搜索起点：局部变量只搜所在函数，其他的（全局）从 root 开始
    parent = var_decl.semantic_parent
    if parent is not None and parent.kind == CursorKind.FUNCTION_DECL:
        search_root = parent
    else:
        search_root = root

    found = False

    def visit(node: Cursor):
        nonlocal found
        if found:
            return

        if node.kind == CursorKind.DECL_REF_EXPR:
            ref = node.referenced
            if ref is not None and ref == var_decl:
                found = True
                return

        for child in node.get_children():
            visit(child)

    visit(search_root)
    return found


def _loc_to_offset(text: str, line: int, col: int) -> int:
    """
    把 (行, 列) 转成字符串 offset。
    libclang 给的是 1-based line/column，我们要转成 Python 字符串的 0-based index。
    """
    lines = text.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        raise ValueError(f"line 超出范围: {line}, 文件总行数: {len(lines)}")

    offset = 0
    # 前面完整的行
    for i in range(line - 1):
        offset += len(lines[i])
    # 当前行的列，列也是从 1 开始
    offset += (col - 1)
    return offset


def delete_first_vardecl(src: str, dst: str) -> bool:
    """
    在 src 里挑一个变量声明删掉，并把结果写到 dst。

    选择策略（按优先级）：
      1. 被“使用过”的局部变量（最容易变成 use of undeclared identifier）
      2. 被“使用过”的全局变量
      3. 未使用的局部变量
      4. 未使用的全局变量

    返回值：
      - True: 成功删掉了一条声明
      - False: 没找到合适的 VAR_DECL
    """
    src_path = Path(src).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"源文件不存在: {src_path}")

    # 解析 AST：parse_c_file 返回 TranslationUnit，这里统一拿 .cursor
    tu = parse_c_file(src_path)
    root = getattr(tu, "cursor", tu)

    # 找到所有 VAR_DECL
    cands = _collect_vardecls(root, src_path)
    if not cands:
        return False

    used_locals: List[Cursor] = []
    used_globals: List[Cursor] = []
    unused_locals: List[Cursor] = []
    unused_globals: List[Cursor] = []

    # 按“是否局部 + 是否使用过”分桶
    for c in cands:
        is_local = _is_local_vardecl(c)
        is_used = _is_used_var(root, c)

        if is_local and is_used:
            used_locals.append(c)
        elif (not is_local) and is_used:
            used_globals.append(c)
        elif is_local and (not is_used):
            unused_locals.append(c)
        else:
            unused_globals.append(c)

    # 按优先级从桶里选一个要删的
    node: Optional[Cursor] = None
    for bucket in (used_locals, used_globals, unused_locals, unused_globals):
        if bucket:
            # 现在先选第一个，之后要做随机可以改成 random.choice(bucket)
            node = bucket[0]
            break

    if node is None:
        return False

    start = node.extent.start
    end = node.extent.end

    # 安全检查：确保这个声明的起始/结束位置都在主文件里
    if not start.file or not end.file:
        return False
    if Path(start.file.name).resolve() != src_path:
        return False
    if Path(end.file.name).resolve() != src_path:
        return False

    # 读取源码
    text = src_path.read_text(encoding="utf-8")

    # 用 (行, 列) 算出在字符串中的偏移
    start_off = _loc_to_offset(text, start.line, start.column)
    end_off = _loc_to_offset(text, end.line, end.column)

    if not (0 <= start_off <= end_off <= len(text)):
        # 万一 clang 给了奇怪的范围，直接放弃这次变异
        return False

    # 真·删除这一段源码（不再塞一个 ';' 占位）
    new_text = text[:start_off] + text[end_off:]

    dst_path = Path(dst).resolve()
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(new_text, encoding="utf-8")

    return True
