#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
H1: ArraySubscriptExpr 越界索引变异（只针对全局 g_XXX 数组）

策略：
  - 找到 base 是“固定长度数组”的下标表达式 a[i]
  - 且数组名以 g_ 开头（Csmith 全局变量，后续使用概率高）
  - 把下标 i 直接替换成明显越界的常量：
        a[i]  ==>  a[N] / a[N+1] / a[N+2]
    其中 N 为数组长度

预期诊断（需要编译时开启 -Warray-bounds 等）：
  - array subscript 4 is outside the bounds of 'int[4]'
"""

import os
import random
from pathlib import Path
from typing import Dict, List, Tuple

from clang import cindex


# --------------------- 通用小工具 ---------------------


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


# --------------------- 收集数组变量 ---------------------


def _collect_array_vars(
    tu: cindex.TranslationUnit,
    seed_path: str,
) -> Dict[str, int]:
    """
    收集当前 seed 文件里的“固定长度数组” VarDecl：
        int a[4];  ->  {'a': 4}
    只看 VarDecl，忽略参数 / struct 字段。
    """
    from clang.cindex import CursorKind, TypeKind

    arrays: Dict[str, int] = {}

    for c in _walk(tu.cursor):
        if c.kind != CursorKind.VAR_DECL:
            continue
        if not _same_file(seed_path, c.location.file):
            continue

        ty = c.type
        if ty.kind == TypeKind.CONSTANTARRAY:
            size = ty.get_array_size()
            if size > 0 and c.spelling:
                arrays[c.spelling] = int(size)

    if arrays:
        items = ", ".join(f"{name}[{size}]" for name, size in sorted(arrays.items()))
        print(f"[debug] fixed-size arrays in seed file: {items}")
    else:
        print("[debug] no fixed-size arrays in seed file")

    return arrays


# --------------------- 从 base 里挖出变量名 ---------------------


def _find_declref_name(cur: cindex.Cursor) -> str:
    """
    在 base 子树里找第一个 DECL_REF_EXPR，当作“数组变量名”。
    适配 g_13[0][1][1] 这种多维数组情况。
    """
    from clang.cindex import CursorKind

    stack = [cur]
    while stack:
        n = stack.pop()
        if n.kind == CursorKind.DECL_REF_EXPR and n.spelling:
            return n.spelling
        stack.extend(list(n.get_children()))
    return ""


def _is_hot_array(name: str) -> bool:
    """
    粗暴地认为 Csmith 里以 g_ 开头的都是全局“热数组”，
    优先对这些做变异，提升“影响范围”。
    """
    return name.startswith("g_")


# --------------------- 收集下标表达式候选 ---------------------


def _collect_array_subscript_candidates(
    tu: cindex.TranslationUnit,
    seed_path: str,
    arrays: Dict[str, int],
    min_depth: int = 1,
) -> List[Tuple[cindex.Cursor, int, str, int]]:
    """
    找到所有形如 a[i] 的 ArraySubscriptExpr，其中 a 对应的是一个
    固定长度数组（通过 base.type 或 arrays 这两种方式识别），
    且数组名满足 _is_hot_array（默认 g_ 开头）。

    返回列表元素：
        (index_cursor, depth, array_name, array_size)
    """
    from clang.cindex import CursorKind, TypeKind

    results: List[Tuple[cindex.Cursor, int, str, int]] = []

    def dfs(node: cindex.Cursor, depth: int):
        if not _same_file(seed_path, node.location.file):
            for ch in node.get_children():
                dfs(ch, depth)
            return

        if node.kind == CursorKind.ARRAY_SUBSCRIPT_EXPR and depth >= min_depth:
            children = list(node.get_children())
            if len(children) >= 2:
                base = children[0]
                index = children[-1]

                # 1) 先从 base 的类型判断是不是 ConstantArray
                arr_size = None
                base_ty = base.type.get_canonical()
                if base_ty.kind == TypeKind.CONSTANTARRAY:
                    s = base_ty.get_array_size()
                    if s > 0:
                        arr_size = int(s)

                # 2) 再从 base 子树里找 DECL_REF_EXPR 名字，用 arrays 兜底
                arr_name = _find_declref_name(base)
                if arr_size is None and arr_name in arrays:
                    arr_size = arrays[arr_name]

                # 3) 只对“热点数组”（g_ 开头）动手
                if arr_name and arr_size is not None and arr_size > 0:
                    if _is_hot_array(arr_name):
                        results.append((index, depth, arr_name, arr_size))

        for ch in node.get_children():
            dfs(ch, depth + 1)

    dfs(tu.cursor, 0)
    print(f"[debug] array-subscript candidates in seed file: {len(results)}")
    return results


# --------------------- 构造越界下标 ---------------------


def _make_oob_index(size: int) -> str:
    """
    给定数组长度 size，构造一个明显越界的常量下标。
    典型做法：
        size, size+1, size+2 ...
    """
    delta = random.randint(0, 2)
    new_idx = size + delta
    return str(new_idx)


# --------------------- 主变异逻辑 ---------------------


def mutate_array_oob_index(in_path: str, out_path: str) -> bool:
    """
    对 in_path 的 C 源文件做 H1 变异，输出到 out_path。
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

    # 打印 AST 诊断（方便比较变异前后差异）
    for d in tu.diagnostics:
        loc = (
            f"{d.location.file}:{d.location.line}:{d.location.column}"
            if d.location.file
            else "<no-file>"
        )
        print(f"[AST diag] {loc}: {d.spelling}")

    arrays = _collect_array_vars(tu, in_path)

    candidates = _collect_array_subscript_candidates(
        tu, in_path, arrays, min_depth=1
    )
    if not candidates:
        print("[debug] no usable array-subscript sites; skip H1 mutation")
        return False

    # 随机打乱，最多选 3 个位置做变异
    random.shuffle(candidates)
    targets = candidates[:3]

    src = Path(in_path).read_text(encoding="utf-8")

    replacements: List[Tuple[int, int, str]] = []
    for idx_cursor, depth, arr_name, arr_size in targets:
        start = idx_cursor.extent.start.offset
        end = idx_cursor.extent.end.offset
        if start is None or end is None:
            continue

        line = idx_cursor.location.line
        new_idx_src = _make_oob_index(arr_size)
        print(
            f"[debug] mutate array index at line {line}, depth={depth}, "
            f"array='{arr_name}[{arr_size}]' -> index '{new_idx_src}'"
        )

        replacements.append((start, end, new_idx_src))

    if not replacements:
        print("[debug] no replacements generated; no mutation applied")
        return False

    # 从后往前替换，避免 offset 变化
    replacements.sort(key=lambda x: x[0], reverse=True)
    for start, end, new_text in replacements:
        src = src[:start] + new_text + src[end:]

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(src, encoding="utf-8")

    print(f"[debug] applied {len(replacements)} array-oob mutations")
    print(f"[结果] 已生成变异文件: {out_path}")
    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="H1: Array 下标越界变异（g_ 全局数组）")
    ap.add_argument("in_file", help="输入 seed 源文件")
    ap.add_argument("out_file", help="输出变异后的源文件")
    args = ap.parse_args()

    mutate_array_oob_index(args.in_file, args.out_file)
