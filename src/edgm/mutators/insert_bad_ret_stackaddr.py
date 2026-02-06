#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
H2: 构造“返回栈上局部变量地址”的 helper 函数，并让已有指针变量接收它。

设计目标（对应你表格中的 H2）：
  - 在 TU 里找到一个已有的指针变量 p（形如 T *p），位于某个函数体内部；
  - 在文件顶部插入一个 helper 函数：

        /* H2: helper that returns address of stack local (bad) */
        static T *edgm_ret_local_ptr(void)
        {
            T x;
            return &x;
        }

    其中 T 与 p 的指向类型保持一致（如 int32_t、struct S0 等）；

  - 在声明 p 的函数体中，在 p 声明之后插入：

        /* H2: store pointer to stack local returned by edgm_ret_local_ptr */
        p = edgm_ret_local_ptr();

  典型诊断（取决于编译器/选项，一般是 warning，可配合 -Werror=… 升级为 error）：
      address of stack memory associated with local variable 'x' returned
      returning address of local variable
      warning: function returns address of local variable [-Wreturn-stack-address]

  不同编译器/版本在是否发出此诊断、以及报错信息和归因位置上都有概率不一致，
  比较适合作为“高级”的诊断一致性测试用例。
"""

import os
import random
from pathlib import Path
from typing import List, Tuple

from clang import cindex


# --------------------- 基础遍历工具 ---------------------


def _same_file(path: str, loc_file) -> bool:
    """判断 AST 位置是否来自同一个源文件。"""
    if loc_file is None:
        return False
    try:
        return os.path.samefile(path, loc_file.name)
    except FileNotFoundError:
        return os.path.basename(path) == os.path.basename(loc_file.name)


def _walk(cursor):
    """DFS 遍历 AST。"""
    yield cursor
    for c in cursor.get_children():
        yield from _walk(c)


# --------------------- 收集候选：函数 + 指针局部变量 ---------------------


def _collect_function_pointer_vars(
    tu: cindex.TranslationUnit, seed_path: str
) -> List[Tuple[cindex.Cursor, cindex.Cursor]]:
    """
    收集形如 “函数定义 + 该函数内的局部 pointer VarDecl” 的候选对。

    返回列表元素为 (function_cursor, var_cursor)。
    """
    pairs: List[Tuple[cindex.Cursor, cindex.Cursor]] = []

    for c in _walk(tu.cursor):
        if c.kind != cindex.CursorKind.FUNCTION_DECL or not c.is_definition():
            continue
        if not _same_file(seed_path, c.location.file):
            continue

        func = c
        # 遍历该函数体内的所有 VarDecl
        for child in _walk(func):
            if child.kind != cindex.CursorKind.VAR_DECL:
                continue
            if not _same_file(seed_path, child.location.file):
                continue

            vtype = child.type
            # 只收集指针类型（避免数组、函数等）
            if vtype.kind != cindex.TypeKind.POINTER:
                continue

            pointee = vtype.get_pointee()
            # 排除 void * 这种不太有趣的情况（容易和别的指针混在一起）
            if pointee.kind == cindex.TypeKind.VOID:
                continue

            # 有名字，才好插入使用
            if not child.spelling:
                continue

            pairs.append((func, child))

    print(f"[debug] H2: candidate (func, pointer-var) pairs: {len(pairs)}")
    return pairs


# --------------------- 主变异逻辑 ---------------------


def insert_bad_ret_stackaddr(in_path: str, out_path: str) -> bool:
    """
    对 in_path 的 C 源文件做 H2 变异，输出到 out_path。
    返回是否实际做了变异。
    """
    in_path = str(in_path)
    out_path = str(out_path)

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

    pairs = _collect_function_pointer_vars(tu, in_path)
    if not pairs:
        print("[debug] H2: no (function, pointer-var) pairs found, skip mutation")
        return False

    # 随机选择一个 (函数, 指针变量) 组合，而不是总用第一个
    func_cursor, var_cursor = random.choice(pairs)
    func_name = func_cursor.spelling or "<anonymous>"
    var_name = var_cursor.spelling
    var_type = var_cursor.type  # T *
    pointee_type = var_type.get_pointee()
    pointee_spelling = pointee_type.spelling or "int"

    print(
        "[debug] H2: choose function '%s', pointer var '%s' (type='%s') at line %d"
        % (
            func_name,
            var_name,
            var_type.spelling,
            var_cursor.location.line,
        )
    )

    # ---------- 读入源文件 ----------
    src_path = Path(in_path)
    src_text = src_path.read_text(encoding="utf-8")
    src_lines = src_text.splitlines(keepends=True)

    # ---------- 1) 在顶部插入 helper 函数 ----------

    helper_name = "edgm_ret_local_ptr"
    helper_snippet = (
        "/* H2: helper that returns address of stack local (bad) */\n"
        f"static {pointee_spelling} *{helper_name}(void)\n"
        "{\n"
        f"    {pointee_spelling} x;\n"
        "    return &x;\n"
        "}\n\n"
    )

    # 尽量插在 #include "csmith.h" 之后；如果不存在，就插在所有 include 之后
    insert_helper_idx = None
    for i, line in enumerate(src_lines):
        if '#include "csmith.h"' in line:
            insert_helper_idx = i + 1
            break
    if insert_helper_idx is None:
        last_inc = -1
        for i, line in enumerate(src_lines):
            if line.lstrip().startswith("#include"):
                last_inc = i
        insert_helper_idx = last_inc + 1

    print(f"[debug] H2: insert helper function at line {insert_helper_idx + 1}")
    src_lines.insert(insert_helper_idx, helper_snippet)

    # 如果 helper 插入的位置在我们稍后要插入赋值语句之前，
    # 则要调整目标行号（因为行数整体向后移动了一段）
    var_decl_line = var_cursor.location.line
    if insert_helper_idx <= var_decl_line - 1:
        helper_lines_count = helper_snippet.count("\n")
        var_decl_line += helper_lines_count
        print(
            f"[debug] H2: adjust var_decl_line to {var_decl_line} "
            f"after inserting helper"
        )

    # ---------- 2) 在指针变量声明之后插入对 helper 的调用 ----------

    assign_insert_line = var_decl_line + 1  # 在声明下一行插入
    assign_snippet = (
        f"    /* H2: store pointer to stack local returned by {helper_name} */\n"
        f"    {var_name} = {helper_name}();\n"
    )

    if assign_insert_line < 1 or assign_insert_line > len(src_lines) + 1:
        print(
            f"[debug] H2: assign_insert_line {assign_insert_line} out of range "
            f"(len={len(src_lines)}), skip mutation"
        )
        return False

    print(
        f"[debug] H2: insert bad assignment after line {var_decl_line} "
        f"in function '{func_name}'"
    )
    src_lines.insert(assign_insert_line - 1, assign_snippet)

    # ---------- 写回输出文件 ----------
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(src_lines), encoding="utf-8")

    print(f"[结果] 已生成 H2 变异文件: {out_path}")
    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description=(
            "H2: 插入返回栈上局部变量地址的 helper 函数，并让已有指针变量接收它"
        )
    )
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_bad_ret_stackaddr(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 H2 变异（可能未找到合适的指针变量）")
