#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
I10: 在深层表达式里插入“复杂非法强制类型转换”（升级版 + 类型限定符）。

目标：
  - 在函数体内部的“深层表达式”上，插入多层乱七八糟的 cast：
      * 各种 int/float/double/指针类型；
      * 加上 const / volatile / restrict 等限定符；
      * 最外层优先 cast 成 struct（必定非法），或者带限定符的标量/指针。
  - 尽量不去动：
      * 全局变量初始化表达式（避免一上来在文件顶端就炸成一团）；
      * 太浅的表达式（保持“深度恶心”）。

典型结果：
  - conversion to non-scalar type requested
  - invalid operands to binary | / & / ^ ...
  - passing ‘XXX *’ makes integer from pointer without a cast ...
"""

import os
import random
from pathlib import Path
from typing import List, Tuple

from clang import cindex

# 如有需要可手动指定 libclang
# cindex.Config.set_library_file("/usr/lib/llvm-14/lib/libclang.so.1")


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


def _is_in_function(node: cindex.Cursor) -> bool:
    """
    只在函数内部做变异：
      - 往上找 semantic_parent，遇到 FUNCTION_DECL 则认为在函数里；
      - 如果直接遇到 TRANSLATION_UNIT，则说明是全局（例如全局初始化），跳过。
    """
    p = node.semantic_parent
    while p is not None:
        if p.kind == cindex.CursorKind.FUNCTION_DECL:
            return True
        if p.kind == cindex.CursorKind.TRANSLATION_UNIT:
            return False
        p = p.semantic_parent
    return False


# --------------------- 收集 struct 类型 ---------------------


def _collect_struct_types(tu: cindex.TranslationUnit, seed_path: str) -> List[str]:
    """
    只收集“出现在种子文件里的 struct 类型”。

    避免把 safe_math.h / stdio.h 里那些 _IO_FILE 之类的系统 struct 也收进来，
    否则 debug 输出很脏，而且用这些类型 cast 也没什么意义。
    """
    names = set()
    for c in _walk(tu.cursor):
        if c.kind == cindex.CursorKind.STRUCT_DECL and c.spelling:
            if _same_file(seed_path, c.location.file):
                names.add(c.spelling)

    if names:
        print(f"[debug] struct types in seed file: {[f'struct {n}' for n in sorted(names)]}")
    else:
        print("[debug] no struct types found in seed file")
    return [f"struct {n}" for n in sorted(names)]


# --------------------- 收集深层表达式节点 ---------------------


# 注意：这里刻意把 UNARY_OPERATOR 排除掉，避免直接把 *p / &g 之类的东西搞成奇怪的 struct，
# 会很容易变成赋值左值、全局初始化元素之类的“超级炸点”，影响整体结构。
_EXPR_KINDS = {
    cindex.CursorKind.BINARY_OPERATOR,
    # cindex.CursorKind.UNARY_OPERATOR,           # 故意不选
    cindex.CursorKind.CALL_EXPR,
    cindex.CursorKind.ARRAY_SUBSCRIPT_EXPR,
    cindex.CursorKind.COMPOUND_ASSIGNMENT_OPERATOR,
    cindex.CursorKind.CONDITIONAL_OPERATOR,
    cindex.CursorKind.CSTYLE_CAST_EXPR,
    cindex.CursorKind.PAREN_EXPR,
}


def _collect_deep_exprs(
    tu: cindex.TranslationUnit,
    seed_path: str,
    min_depth: int = 3,
) -> List[Tuple[cindex.Cursor, int]]:
    """
    收集在 seed 文件内、且嵌套深度 >= min_depth 的表达式节点，
    且限定在“函数体内部”（避免全局初始化）。
    """
    results: List[Tuple[cindex.Cursor, int]] = []

    def dfs(node: cindex.Cursor, depth: int):
        if not _same_file(seed_path, node.location.file):
            # 只变异种子源文件本身
            for ch in node.get_children():
                dfs(ch, depth)
            return

        if node.kind in _EXPR_KINDS and depth >= min_depth and _is_in_function(node):
            results.append((node, depth))

        for ch in node.get_children():
            dfs(ch, depth + 1)

    dfs(tu.cursor, 0)
    print(f"[debug] deep expr candidates in seed file: {len(results)}")
    return results


# --------------------- 构造“复杂非法 cast 文本” ---------------------


# 基础类型池（不含限定符），后面再统一加 const/volatile/restrict
_SCALAR_AND_PTR_BASE_TYPES = [
    # 整型
    "int8_t",
    "uint8_t",
    "int16_t",
    "uint16_t",
    "int32_t",
    "uint32_t",
    "int64_t",
    "uint64_t",
    "char",
    "signed char",
    "unsigned char",
    # 浮点
    "float",
    "double",
    # 指针类型
    "void *",
    "int8_t *",
    "uint8_t *",
    "int32_t *",
    "uint32_t *",
    "int64_t *",
    "uint64_t *",
    "float *",
    "double *",
]


def _with_qualifiers(base: str) -> str:
    """
    在给定类型串 base 上随机加上 const / volatile / restrict：
      - 对所有类型：前面随机加 "", "const ", "volatile ", "const volatile " 之一；
      - 如果是指针类型（简单靠 '*' 判断），再有概率在末尾加 ' restrict'。
    """
    cv_choices = ["", "const ", "volatile ", "const volatile "]
    cv = random.choice(cv_choices)

    ty = f"{cv}{base}"

    # 简单判断是否为指针类型：包含 '*' 即认为是指针
    if "*" in base and random.random() < 0.5:
        ty = f"{ty} restrict"

    return ty.strip()


def _build_illegal_cast_chain(expr_src: str, struct_types: List[str]) -> str:
    """
    把原始表达式 expr_src 包装成多层 cast 链：
        ((Tn)(Tn-1)...(T1)(expr_src))

    其中：
      - T1..Tn-1 从 “带限定符的 标量/指针类型池” 里随机选若干个；
      - Tn（最外层）：
          * 如果有 struct 类型：
              - 60% 概率：选一个 struct，并加 const/volatile（不加 restrict）:
                    const struct S0
                    volatile struct S1
                    const volatile struct S2
                -> 必然非法 cast，稳拿错误
              - 40% 概率：再从标量/指针池里选一个（带限定符），
                制造 int/float/指针混合乱 cast。
          * 如果没有 struct 类型：只能从标量/指针池（带限定符）里选。
    """
    # 中间随机 2~4 层，让表达式看起来更复杂
    mid_len = random.randint(2, 4)
    mid_types = [_with_qualifiers(random.choice(_SCALAR_AND_PTR_BASE_TYPES)) for _ in range(mid_len)]

    # 决定最外层类型
    if struct_types and random.random() < 0.6:
        # 以较大概率选择 struct 做最外层非法 cast
        base = random.choice(struct_types)
        # 对 struct 只加 const/volatile，不加 restrict（struct 本身不是指针）
        final_type = _with_qualifiers(base)
        reason = "use struct as final illegal cast target (with qualifiers)"
    else:
        base = random.choice(_SCALAR_AND_PTR_BASE_TYPES)
        final_type = _with_qualifiers(base)
        reason = "use scalar/pointer as final cast target (with qualifiers)"

    print(f"[debug] illegal cast chain final type: {final_type} ({reason})")

    cast_types = mid_types + [final_type]

    wrapped = expr_src
    for ty in cast_types:
        wrapped = f"(({ty})({wrapped}))"

    return wrapped


# --------------------- 主变异逻辑 ---------------------


def insert_illegal_cast(in_path: str, out_path: str) -> bool:
    """
    对 in_path 的 C 源文件做 I10 变异，输出到 out_path。
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

    # 打印一下 AST 诊断（都是 warning / note，不影响我们变异）
    for d in tu.diagnostics:
        loc = (
            f"{d.location.file}:{d.location.line}:{d.location.column}"
            if d.location.file
            else "<no-file>"
        )
        print(f"[AST diag] {loc}: {d.spelling}")

    struct_types = _collect_struct_types(tu, in_path)
    # 即便没有 struct，也可以做“乱 cast”，所以不直接退出

    expr_candidates = _collect_deep_exprs(tu, in_path, min_depth=3)
    if not expr_candidates:
        print("[debug] no deep expr candidates; skip illegal cast mutation")
        return False

    # 随机选几个目标表达式（最多 3 个）
    random.shuffle(expr_candidates)
    targets = expr_candidates[:3]

    src = Path(in_path).read_text(encoding="utf-8")

    # 为了按 offset 替换，先按起始 offset 排序，后面从后往前替换
    replacements = []
    for cur, depth in targets:
        start = cur.extent.start.offset
        end = cur.extent.end.offset
        if start is None or end is None:
            continue

        line = cur.location.line
        kind_name = cur.kind.name
        print(f"[debug] insert illegal cast at line {line}, depth={depth}, kind={kind_name}")

        orig_text = src[start:end]
        cast_text = _build_illegal_cast_chain(orig_text, struct_types)
        replacements.append((start, end, cast_text))

    if not replacements:
        print("[debug] no usable targets after filtering; no mutation applied")
        return False

    # 从后向前替换，避免 offset 失效
    replacements.sort(key=lambda x: x[0], reverse=True)
    for start, end, new_text in replacements:
        src = src[:start] + new_text + src[end:]

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(src, encoding="utf-8")

    print(f"[debug] applied {len(replacements)} illegal cast mutations")
    print(f"[结果] 已生成变异文件: {out_path}")
    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="I10: 插入复杂非法强制类型转换变异（带限定符）")
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    insert_illegal_cast(args.in_file, args.out_file)
