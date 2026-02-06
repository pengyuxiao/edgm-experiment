#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
P13-adv: 在已有普通函数定义前插入一个带 inline/extern inline 的
        “错误返回类型”原型声明，制造 inline + 类型冲突。

效果示例（原始）:

    static uint32_t func_1(void) {
        ...
    }

变异后:

    /* P13-adv: conflicting inline declaration for func_1 */
    inline void * func_1(void);

    static uint32_t func_1(void) {
        ...
    }

典型诊断:
    error: conflicting types for 'func_1'
    note: previous declaration of 'func_1' was here
"""

import os
from pathlib import Path
from typing import List
import random
from clang import cindex


# --------------------- 基础工具 ---------------------


def _same_file(path: str, loc_file) -> bool:
    """判断某个 AST 节点是否来自 seed 源文件本身。"""
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


# --------------------- 收集函数定义 ---------------------


def _collect_func_defs(tu: cindex.TranslationUnit, seed_path: str) -> List[cindex.Cursor]:
    """
    收集 seed 源文件里的 FunctionDecl 定义（排除只声明不定义的）。
    """
    funcs: List[cindex.Cursor] = []
    for c in _walk(tu.cursor):
        if c.kind == cindex.CursorKind.FUNCTION_DECL and c.is_definition():
            if _same_file(seed_path, c.location.file):
                funcs.append(c)

    print(f"[debug] function definitions in seed file: {len(funcs)}")
    return funcs


def _build_param_list(func: cindex.Cursor) -> str:
    """
    把形参列表转成 C 源码字符串（只要类型 + 名字，不展开默认参数等）。
    """
    params = []
    for arg in func.get_arguments():
        ty = arg.type.spelling or "int"
        name = arg.spelling or ""
        if name:
            params.append(f"{ty} {name}")
        else:
            params.append(ty)

    if not params:
        return "void"
    return ", ".join(params)


def _choose_conflicting_ret_type(orig_ret: str) -> str:
    """
    根据原始返回类型，选择一个“明显不同”的返回类型。

    简单但鲁棒的策略：
      - 如果原来是 void 系列，用 'int32_t'。
      - 否则用 'void *'。
    """
    s = orig_ret.replace("const", "").replace("volatile", "").strip()
    s = " ".join(s.split())

    if s == "void":
        bad = "int32_t"
    else:
        bad = "void *"

    # 极端情况兜底：万一刚好相等，再换一种
    if bad == orig_ret:
        bad = "long long"

    return bad


def _build_conflicting_prototype(func: cindex.Cursor) -> str:
    """
    构造带 inline/extern inline 的冲突原型声明。
    只改返回类型，不动参数，这样错误来源更清晰。
    """
    orig_ret = func.result_type.spelling or "int"
    name = func.spelling
    params = _build_param_list(func)

    bad_ret = _choose_conflicting_ret_type(orig_ret)

    # 玩一点花样：有一定概率用 extern inline，有一定概率用 plain inline。
    # 这样有机会触发不同的 inline 语义分支。
    storage = random.choice(["inline", "extern inline"])
    # 你也可以改成随机:
    # import random
    # storage = random.choice(["inline", "extern inline"])

    proto = f"{storage} {bad_ret} {name}({params});"

    print(
        f"[debug] P13-adv: choose function '{name}', "
        f"orig_ret='{orig_ret}', bad_ret='{bad_ret}', params='({params})'"
    )
    print(f"[debug] P13-adv: conflicting prototype: '{proto}'")
    return proto


# --------------------- 主变异逻辑 ---------------------


def mutate_func_inline_conflict_adv(in_path: str, out_path: str) -> bool:
    """
    对 in_path 的 C 源做 P13-adv 变异：
      - 选一个函数定义 f；
      - 在 f 的定义前插入一行 inline / extern inline 冲突原型；
      - 输出到 out_path。
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

    # 打印 AST 诊断（原始 seed 里自带的一堆 warning，可以忽略）
    for d in tu.diagnostics:
        loc = (
            f"{d.location.file}:{d.location.line}:{d.location.column}"
            if d.location.file
            else "<no-file>"
        )
        print(f"[AST diag] {loc}: {d.spelling}")

    funcs = _collect_func_defs(tu, in_path)
    if not funcs:
        print("[debug] no function definitions found; skip P13-adv mutation")
        return False

    # 简单点：选第一个函数（通常是 func_1）
    target = funcs[0]
    name = target.spelling
    start_line = target.extent.start.line
    print(f"[debug] P13-adv: target function '{name}' starts at line {start_line}")

    proto = _build_conflicting_prototype(target)

    # 读源文件，按行插入
    src = Path(in_path).read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)

    insert_idx = max(0, start_line - 1)
    snippet = f"/* P13-adv: conflicting inline declaration for {name} */\n{proto}\n"
    lines.insert(insert_idx, snippet)

    mutated = "".join(lines)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(mutated, encoding="utf-8")

    print(f"[结果] 已生成 P13-adv 变异文件: {out_path}")
    return True
