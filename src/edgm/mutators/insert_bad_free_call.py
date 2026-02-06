#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
P14: 在函数体里插入错误的 free(函数指针) 调用。

设计目标：
  - 在 TU 里找到一个普通有定义的 FunctionDecl（bar）
  - 从现有的函数列表里选一个函数名 foo
  - 在 bar 的某个 CompoundStmt 中插入：

        /* P14: pass function pointer to free (incompatible with void *) */
        free(foo);

    其中：
      - foo 是一个函数标识符，类型类似 int (*)(int)
      - free 的原型是 void free(void *);   （期望接受对象指针）
      - 函数指针和 void* 是不兼容的指针类型

  典型诊断（Clang/GCC 都会报 warning，配合 -Werror=incompatible-pointer-types
  可以升级成 error）：
      incompatible pointer type passing 'int (*)(int)' to parameter of type 'void *'
"""

import os
from pathlib import Path
from typing import List
import random
from clang import cindex


# --------------------- 基础遍历工具 ---------------------


def _same_file(path: str, loc_file) -> bool:
    if loc_file is None:
        return False
    try:
        return os.path.samefile(path, loc_file.name)
    except FileNotFoundError:
        # 有些系统下 name 可能不是绝对路径，这里退化成 basename 比较
        return os.path.basename(path) == os.path.basename(loc_file.name)


def _walk(cursor):
    """DFS 遍历 AST。"""
    yield cursor
    for c in cursor.get_children():
        yield from _walk(c)


# --------------------- 收集函数定义 / 函数名 ---------------------


def _collect_function_defs(
    tu: cindex.TranslationUnit,
    seed_path: str,
) -> List[cindex.Cursor]:
    """收集当前 seed 文件里，有函数体的 FunctionDecl。"""
    funs: List[cindex.Cursor] = []
    for c in _walk(tu.cursor):
        if c.kind == cindex.CursorKind.FUNCTION_DECL and c.is_definition():
            if _same_file(seed_path, c.location.file):
                # 排除内联的内建函数等奇怪东西（一般不会出现在 seed 里）
                spelling = c.spelling or ""
                if spelling.startswith("__builtin_"):
                    continue
                funs.append(c)
    print(f"[debug] function definitions in seed file: {len(funs)}")
    return funs


# --------------------- 主变异逻辑 ---------------------


def insert_bad_free_call(in_path: str, out_path: str) -> bool:
    """
    对 in_path 的 C 源文件做 P14 变异，输出到 out_path。
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

    fun_defs = _collect_function_defs(tu, in_path)
    if len(fun_defs) < 1:
        print("[debug] no function definitions, skip P14 mutation")
        return False

    # 为了简单起见，这里就用第一个函数作为 bar，第二个（若有）作为 foo；
    # 如果只有一个函数，就自己 free 自己。
    # 随机选一个作为 target（插 free 的函数）
    target_fun = random.choice(fun_defs)

    # 再随机选一个作为被 free 的函数名，尽量和 target 不同
    if len(fun_defs) > 1:
        candidates = [f for f in fun_defs if f is not target_fun]
        arg_fun = random.choice(candidates)
    else:
        arg_fun = target_fun
    # ---------- 新逻辑结束 ---------

    target_name = target_fun.spelling or "<anonymous>"
    arg_name = arg_fun.spelling or "<anonymous>"

    print(
        f"[debug] P14: choose target function '{target_name}', "
        f"argument function '{arg_name}'"
    )

    # 找到 target_fun 的函数体 CompoundStmt
    body = None
    for ch in target_fun.get_children():
        if ch.kind == cindex.CursorKind.COMPOUND_STMT:
            body = ch
            break

    if body is None:
        print("[debug] target function has no compound body; skip P14 mutation")
        return False

    # 在函数体末尾的 '}' 之前插入一段代码。
    insert_line = body.extent.end.line  # 1-based
    print(
        f"[debug] P14: insert bad free() call before line {insert_line} "
        f"in function '{target_name}'"
    )

    # 构造插入片段（缩进按 4 空格写死就行）
    snippet = (
        "    /* P14: pass function pointer to free (incompatible with void *) */\n"
        f"    free({arg_name});\n"
    )

    # 按行插入
    src_lines = Path(in_path).read_text(encoding="utf-8").splitlines(keepends=True)

    need_stdlib = True
    for line in src_lines:
        if "#include <stdlib.h>" in line:
            need_stdlib = False
            break

    if need_stdlib:
        # 尽量插在 #include "csmith.h" 后面，如果找不到就插在所有 include 的最后
        insert_inc_idx = None
        for i, line in enumerate(src_lines):
            if '#include "csmith.h"' in line:
                insert_inc_idx = i + 1
                break
        if insert_inc_idx is None:
            # 找当前最后一个 #include
            last_inc = -1
            for i, line in enumerate(src_lines):
                if line.lstrip().startswith("#include"):
                    last_inc = i
            insert_inc_idx = last_inc + 1

        src_lines.insert(insert_inc_idx, "#include <stdlib.h>\n")

        # 如果我们在函数体之前插入了这一行，要把插入 free 的行号 +1
        if insert_inc_idx < insert_line - 1:
            insert_line += 1
    # --- 新增结束 ---

    # 防御性：防止越界
    if insert_line < 1 or insert_line > len(src_lines) + 1:
        print("[debug] body extent line out of range; skip P14 mutation")
        return False

    src_lines.insert(insert_line - 1, snippet)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("".join(src_lines), encoding="utf-8")

    print(f"[结果] 已生成 P14 变异文件: {out_path}")
    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="P14: 在函数体中插入错误的 free(函数指针) 调用")
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    ok = insert_bad_free_call(args.in_file, args.out_file)
    if not ok:
        print("[结果] 未进行 P14 变异（可能未找到可用的函数定义）")
