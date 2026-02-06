#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
P21: 全局变量错误使用（wrong-type use of global variable）

策略：
  - 在 seed 文件中找到一个全局变量 VarDecl（只考虑当前 .c 文件里的，排除头文件）。
  - 在某个已有函数定义（FunctionDecl with body）里，函数体关闭大括号前，
    插入一条“明显类型错误”的使用语句，比如：
        (void)(g_foo + g_foo);    // g_foo 是 struct / 指针 / 数组 等非标量
        (void)(*g_foo);           // g_foo 是 int / uint8_t 等标量

  - 根据全局变量的类型分类来挑选不同的“错用模板”：
        * 标量整数类型：         (void)(*g);
        * 指针类型：             (void)(g + g);   // 指针 + 指针 非法
        * struct / union 类型：  (void)(g + g);   // struct + struct 非法
        * 其他：                 (void)(g + g);   // 兜底

  典型诊断（gcc）：
    - invalid operands to binary + (have 'struct S0' and 'struct S0')
    - indirection requires pointer operand ('int' invalid as unary '*')

  注意：
    - 我们不修改原来的 VarDecl，只是在使用处插入一条“故意写错”的表达式，
      这样错误定位更偏向语义/类型检查，而不是简单的重复定义。
"""

import os
import random
from pathlib import Path
from typing import List, Tuple, Optional

from clang import cindex


# --------------------- 基础遍历工具 ---------------------


def _same_file(path: str, loc_file) -> bool:
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


# --------------------- 收集全局变量 & 函数定义 ---------------------


def _collect_global_vardecls(tu: cindex.TranslationUnit, seed_path: str):
    globals_ = []
    for c in _walk(tu.cursor):
        if c.kind == cindex.CursorKind.VAR_DECL and _same_file(seed_path, c.location.file):
            # 只要“翻译单元的直接子节点”作为真正的全局变量
            if c.semantic_parent and c.semantic_parent.kind == cindex.CursorKind.TRANSLATION_UNIT:
                globals_.append(c)
    print(f"[debug] global VarDecl in seed file: {len(globals_)}")
    return globals_


def _collect_func_defs(tu: cindex.TranslationUnit, seed_path: str):
    funcs = []
    for c in _walk(tu.cursor):
        if c.kind == cindex.CursorKind.FUNCTION_DECL and c.is_definition():
            if _same_file(seed_path, c.location.file):
                funcs.append(c)
    print(f"[debug] function definitions in seed file: {len(funcs)}")
    return funcs


# --------------------- 类型分类 & 错误使用模板 ---------------------


def _classify_type(typ: cindex.Type) -> str:
    """
    粗略把类型分为几类：
      - 'scalar_int'  : 各种整型
      - 'pointer'     : 指针类型
      - 'record'      : struct / union
      - 'other'       : 其他兜底
    """
    canonical = typ.get_canonical()

    if canonical.kind in (
        cindex.TypeKind.BOOL,
        cindex.TypeKind.CHAR_U,
        cindex.TypeKind.UCHAR,
        cindex.TypeKind.CHAR_S,
        cindex.TypeKind.SCHAR,
        cindex.TypeKind.SHORT,
        cindex.TypeKind.USHORT,
        cindex.TypeKind.INT,
        cindex.TypeKind.UINT,
        cindex.TypeKind.LONG,
        cindex.TypeKind.ULONG,
        cindex.TypeKind.LONGLONG,
        cindex.TypeKind.ULONGLONG,
        cindex.TypeKind.ENUM,
    ):
        return "scalar_int"

    if canonical.kind == cindex.TypeKind.POINTER:
        return "pointer"

    if canonical.kind in (cindex.TypeKind.RECORD,):
        # struct / union 都算 RECORD
        return "record"

    return "other"


def _build_wrong_use_snippet(var_name: str, kind: str) -> str:
    """
    根据全局变量的类型类别，生成一段“必错”的使用代码（带注释和缩进）。
    """
    comment = f"/* P21-use: wrong-type use of {var_name} ({kind}) */"

    if kind == "scalar_int":
        # 标量整数，写成 *g_foo
        expr = f"(void)(*{var_name});"
    else:
        # 指针 / struct / 其他，写成 g_foo + g_foo
        expr = f"(void)({var_name} + {var_name});"

    # 使用 4 个空格缩进，插入到函数体内部
    return f"    {comment}\n    {expr}\n"


# --------------------- 主变异逻辑 ---------------------


def mutate_global_use_wrong_type(in_path: str, out_path: str) -> bool:
    """
    在 in_path 中：
      - 选一个全局变量 g_name
      - 选一个函数定义 func
      - 在 func 的结束大括号之前插入一段“错误使用 g_name”的代码

    成功变异则写入 out_path，并返回 True；否则返回 False。
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

    # 打印一下 AST 诊断（warning 为主）
    for d in tu.diagnostics:
        loc = (
            f"{d.location.file}:{d.location.line}:{d.location.column}"
            if d.location.file
            else "<no-file>"
        )
        print(f"[AST diag] {loc}: {d.spelling}")

    globals_ = _collect_global_vardecls(tu, in_path)
    funcs = _collect_func_defs(tu, in_path)

    if not globals_:
        print("[debug] no global VarDecl; skip P21 mutation")
        return False
    if not funcs:
        print("[debug] no function definitions; skip P21 mutation")
        return False

    # 随机选一个全局变量
    g = random.choice(globals_)
    g_name = g.spelling or "<anon>"
    g_type_kind = _classify_type(g.type)
    print(f"[debug] choose global '{g_name}' type='{g.type.spelling}' classified as '{g_type_kind}'")

    # 随机选一个函数定义，用来插入错误使用
    func = random.choice(funcs)
    f_name = func.spelling or "<anon>"
    start_line = func.extent.start.line
    end_line = func.extent.end.line
    print(f"[debug] choose function '{f_name}' at lines {start_line}..{end_line}")

    # 插入位置：函数体结束大括号所在行的前一行（通常是 '}' 那行）
    # 由于 extent.end.line 就是 '}' 那行，这里直接在 end_line 之前插入即可。
    insert_before_line = end_line  # 在该行之前插入
    snippet = _build_wrong_use_snippet(g_name, g_type_kind)
    print("[debug] insert wrong-type use:\n" + snippet.rstrip())
    print(f"[debug] inserting before original line {insert_before_line}")

    # 读原文件，按行插入
    src_path = Path(in_path)
    lines = src_path.read_text(encoding="utf-8").splitlines(keepends=True)

    if insert_before_line < 1 or insert_before_line > len(lines) + 1:
        print("[debug] computed insert line out of range; skip mutation")
        return False

    idx = insert_before_line - 1  # Python 下标
    lines.insert(idx, snippet)

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("".join(lines), encoding="utf-8")

    print(f"[结果] 已生成 P21-use 变异文件: {out_path}")
    return True
