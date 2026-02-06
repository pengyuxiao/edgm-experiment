#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
I9: 在已有的数组下标表达式里，把 base 换成一个标量全局变量，
    让类似 a[k] 变成 g_scalar[k]，其中 g_scalar 不是数组/指针，
    从而触发：

    subscripted value is neither array nor pointer nor vector

设计思路（综合原方案 1 + 4）：
  - 用 clang AST 找出：
      1) 所有“标量类型”的全局变量（非指针、非数组），例如 int/long 等；
      2) 当前文件里所有 ArraySubscriptExpr（a[k]）
         并且它们的 base 表达式在同一行，方便直接做文本替换。
  - 随机选一个标量全局 gX，随机选一个下标表达式 a[k]，
    把这一行里 a 的源码范围替换成 gX /* I9: bad array subscript on scalar */，
    生成：

        gX /* I9: bad array subscript on scalar */[k]

  - 不插入新语句，而是“就地改写”原来的表达式，
    这样错误处在原始复杂表达式内部，更接近真实代码。

用法（由脚本调用）：
    from edgm.mutators.insert_bad_array_subscript import insert_bad_array_subscript
    insert_bad_array_subscript("in.c", "out.c", seed=123)
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import List, Optional, Tuple

from clang import cindex


# --------------------- 基础工具 ---------------------


def _same_file(path: str, loc_file) -> bool:
    if loc_file is None:
        return False
    try:
        return os.path.samefile(path, loc_file.name)
    except FileNotFoundError:
        return os.path.basename(path) == os.path.basename(loc_file.name)


def _walk(cursor):
    """DFS 遍历 AST 所有节点。"""
    yield cursor
    for c in cursor.get_children():
        yield from _walk(c)


def _enclosing_function_name(cur: cindex.Cursor) -> str:
    """向上找最近的 FunctionDecl 名字，用于 debug。"""
    p = cur.semantic_parent
    while p is not None and p.kind != cindex.CursorKind.FUNCTION_DECL:
        p = p.semantic_parent
    if p is None:
        return "<toplevel>"
    return p.spelling or "<anonymous>"


# --------------------- 收集标量全局变量 ---------------------


def _collect_scalar_globals(
    tu: cindex.TranslationUnit, seed_path: str
) -> List[cindex.Cursor]:
    """
    收集当前文件里“标量”全局变量：
      - CursorKind.VAR_DECL
      - semantic_parent 是顶层（tu.cursor）
      - 类型不是指针/数组
    """
    globals_: List[cindex.Cursor] = []
    for c in _walk(tu.cursor):
        if c.kind != cindex.CursorKind.VAR_DECL:
            continue
        if not _same_file(seed_path, c.location.file):
            continue
        # 只要顶层的全局变量
        if c.semantic_parent is not None and c.semantic_parent != tu.cursor:
            continue

        t = c.type.get_canonical()
        if t.kind in (
            cindex.TypeKind.POINTER,
            cindex.TypeKind.INCOMPLETEARRAY,
            cindex.TypeKind.CONSTANTARRAY,
            cindex.TypeKind.VARIABLEARRAY,
            cindex.TypeKind.DEPENDENTSIZEDARRAY,
        ):
            continue

        # 其它类型（包括 int/long/struct 等）都算“非数组/非指针”
        globals_.append(c)

    print(f"[debug] I9: scalar-like global vars found: {len(globals_)}")
    return globals_


# --------------------- 收集 ArraySubscriptExpr ---------------------


class SubscriptSite:
    def __init__(
        self,
        line: int,
        start_col: int,
        end_col: int,
        func_name: str,
    ) -> None:
        self.line = line
        self.start_col = start_col
        self.end_col = end_col
        self.func_name = func_name

    def __repr__(self) -> str:
        return (
            f"SubscriptSite(line={self.line}, "
            f"cols={self.start_col}..{self.end_col}, "
            f"func={self.func_name!r})"
        )


def _collect_array_subscripts(
    tu: cindex.TranslationUnit, seed_path: str
) -> List[SubscriptSite]:
    """
    收集可以安全改写的 ArraySubscriptExpr：
      - a[k] 的 base 部分在同一行（便于文本替换）
    我们只记录 base 的范围（行号 + 起止列）。
    """
    sites: List[SubscriptSite] = []

    for cur in _walk(tu.cursor):
        if cur.kind != cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR:
            continue
        if not _same_file(seed_path, cur.location.file):
            continue

        # children[0] 是 base，children[1] 是 index
        children = [ch for ch in cur.get_children()]
        if len(children) < 2:
            continue
        base = children[0]

        if base.location.file is None or not _same_file(seed_path, base.location.file):
            continue

        # 只处理 base 在单行上的情况，避免多行替换的复杂度
        if base.extent.start.line != base.extent.end.line:
            continue

        line = base.extent.start.line
        start_col = base.extent.start.column
        end_col = base.extent.end.column  # clang 一般是“末尾列之后”，按切片处理

        func_name = _enclosing_function_name(cur)
        site = SubscriptSite(line, start_col, end_col, func_name)
        sites.append(site)

    print(f"[debug] I9: candidate ArraySubscriptExpr sites: {len(sites)}")
    return sites


# --------------------- 主变异逻辑 ---------------------


def insert_bad_array_subscript(
    in_path: str, out_path: str, seed: Optional[int] = None
) -> bool:
    """
    对 in_path 的 C 源文件做 I9 变异，输出到 out_path。
    返回是否实际做了变异。
    """
    in_path = str(in_path)
    out_path = str(out_path)

    if seed is not None:
        random.seed(seed)

    index = cindex.Index.create()
    tu = index.parse(
        in_path,
        args=[
            "-I",
            "src/csmithedge/csmith/runtime",
            "-I",
            "src/csmithedge/csmith/runtime/..",
            "-std=c99",
        ],
        options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
    )

    # 打印 AST 诊断，方便 debug
    for d in tu.diagnostics:
        loc = (
            f"{d.location.file}:{d.location.line}:{d.location.column}"
            if d.location.file
            else "<no-file>"
        )
        print(f"[AST diag] {loc}: {d.spelling}")

    scalar_globals = _collect_scalar_globals(tu, in_path)
    subscript_sites = _collect_array_subscripts(tu, in_path)

    if not scalar_globals:
        print("[debug] I9: no scalar-like globals found, skip mutation")
        return False
    if not subscript_sites:
        print("[debug] I9: no suitable ArraySubscriptExpr, skip mutation")
        return False

    # 随机选一个全局标量变量
    g_cur = random.choice(scalar_globals)
    g_name = g_cur.spelling or "<anonymous>"
    g_line = g_cur.location.line if g_cur.location else -1
    print(f"[debug] I9: choose scalar global '{g_name}' (line {g_line})")

    # 随机选一个下标表达式位置
    site = random.choice(subscript_sites)
    print(
        "[debug] I9: mutate subscript base at "
        f"line {site.line}, cols {site.start_col}..{site.end_col}, "
        f"in function '{site.func_name}'"
    )

    # 读源文件
    src_lines = Path(in_path).read_text(encoding="utf-8").splitlines(keepends=True)

    if site.line < 1 or site.line > len(src_lines):
        print("[debug] I9: chosen site line out of range, skip mutation")
        return False

    line = src_lines[site.line - 1]

    # clang 的 column 是 1-based；这里假设 end_col 是“末尾之后”，
    # 采用 [start_col-1 : end_col-1) 切片。
    start_idx = max(site.start_col - 1, 0)
    end_idx = max(site.end_col - 1, start_idx)

    prefix = line[:start_idx]
    # old_base = line[start_idx:end_idx]  # 仅用于 debug，需要的话可以打印
    suffix = line[end_idx:]

    replacement = f"{g_name} /* I9: bad array subscript on scalar */"

    new_line = prefix + replacement + suffix
    src_lines[site.line - 1] = new_line

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("".join(src_lines), encoding="utf-8")

    print(f"[结果] 已生成 I9 变异文件: {out_path}")
    return True


# --------------------- CLI ---------------------


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description=(
            "I9: 将已有的 a[k] 里的 base 改成标量全局，"
            "制造 subscripted value is neither array nor pointer 错误"
        )
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="随机种子（可选，用于复现）",
    )
    args = ap.parse_args()

    ok = insert_bad_array_subscript(args.in_file, args.out_file, seed=args.seed)
    if not ok:
        print("[结果] 未进行 I9 变异（可能未找到可用的全局或下标表达式）")
