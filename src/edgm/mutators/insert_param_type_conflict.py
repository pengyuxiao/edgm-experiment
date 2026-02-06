# src/edgm/mutators/insert_param_type_conflict.py
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import List, Tuple

from clang.cindex import Index, Cursor, CursorKind  # type: ignore

_SCALAR_TYPES = [
    "int8_t", "uint8_t",
    "int16_t", "uint16_t",
    "int32_t", "uint32_t",
    "int64_t", "uint64_t",
    "int", "unsigned int",
    "short", "unsigned short",
    "long", "unsigned long",
    "char", "signed char", "unsigned char",
    "float", "double",
]

_POINTER_TYPES = [
    "int8_t *", "uint8_t *",
    "int16_t *", "uint16_t *",
    "int32_t *", "uint32_t *",
    "int64_t *", "uint64_t *",
    "int *", "unsigned int *",
    "char *", "signed char *", "unsigned char *",
    "void *",
]

_ALL_TYPES = _SCALAR_TYPES + _POINTER_TYPES

def _same_file(path1: str, path2: str) -> bool:
    """只按文件名判断是否同一个 seed 文件（路径可能不同）。"""
    return os.path.basename(path1) == os.path.basename(path2)


def _collect_param_candidates(tu: Cursor, seed_path: str) -> List[Cursor]:
    """
    收集所有“在 seed 文件里的函数定义的形参”作为候选变异点。
    只改函数定义里的参数，不改前面的原型声明，这样才能触发 conflicting types。
    """
    candidates: List[Cursor] = []

    for c in tu.walk_preorder():
        if c.kind is not CursorKind.FUNCTION_DECL:
            continue
        if not c.is_definition():
            # 只要定义；原型不改
            continue
        if not c.location.file:
            continue
        if not _same_file(c.location.file.name, seed_path):
            # 只动 seed_000X.c 里的函数，忽略 runtime 之类的
            continue

        # 收集这个函数定义里的所有参数
        for p in c.get_arguments():
            # 没名字的参数没什么意义，跳过
            if not p.spelling:
                continue
            if not p.location.file:
                continue
            if not _same_file(p.location.file.name, seed_path):
                continue

            # 太变态的类型（函数指针、数组等）就先不碰了，降低变异失败概率
            type_text = p.type.spelling
            if "(*" in type_text:   # 函数指针
                continue

            candidates.append(p)

    return candidates

def _strip_cv_qualifiers(t: str) -> str:
    """去掉 const/volatile/restrict，只保留“核心类型”方便比较。"""
    tokens = t.replace("*", " * ").split()
    filtered = [tok for tok in tokens
                if tok not in ("const", "volatile", "restrict")]
    return " ".join(filtered)


def _build_new_param_type(old_type: str) -> str:
    """
    根据旧类型挑一个“尽量离谱”的新类型：
    - 优先制造 标量 <-> 指针 的互换（最容易在位运算、比较等地方炸）
    - 也会引入 float/double/char 之类，和 safe_math 的整型操作冲突
    """
    old_type_norm = " ".join(old_type.split())
    old_core = _strip_cv_qualifiers(old_type_norm)

    old_is_ptr = "*" in old_core

    # 候选类型里先排除“核心类型相同”的（比如原来就是 int32_t，就不要再选 int32_t）
    candidates = [t for t in _ALL_TYPES
                  if _strip_cv_qualifiers(t) != old_core]

    if not candidates:
        # 理论上不会走到这里，保险起见：
        return old_type + " *"

    # 更狠一点：尽量让“指针 vs 非指针”变掉
    if old_is_ptr:
        # 原来是指针，就优先选非指针
        strong = [t for t in candidates if "*" not in t]
    else:
        # 原来是标量，就优先选各种指针
        strong = [t for t in candidates if "*" in t]

    pool = strong or candidates
    new_type = random.choice(pool)

    return new_type


def _apply_edits(src: str, edits: List[Tuple[int, int, str]]) -> str:
    """
    按 offset 对源代码做区间替换。
    edits: (start, end, new_text)
    """
    if not edits:
        return src

    # 按起始位置排序，防止覆盖错乱
    edits_sorted = sorted(edits, key=lambda e: e[0])

    out_chunks: List[str] = []
    last = 0
    for start, end, text in edits_sorted:
        out_chunks.append(src[last:start])
        out_chunks.append(text)
        last = end
    out_chunks.append(src[last:])

    return "".join(out_chunks)


def insert_param_type_conflict(in_path: str, out_path: str) -> bool:
    """
    I6：ParmVarDecl 形参类型冲突变异

    步骤：
    1. 用 clang 解析 seed_000X.c。
    2. 找出所有“在 seed 文件里的函数定义的参数”作为候选。
    3. 随机选一个参数，把它的类型改成完全不同（int32_t <-> int32_t*）。
       只改定义里的参数，不改前面的函数原型，从而触发：
       - conflicting types for 'foo'
       - 或者后续调用处参数类型不匹配等诊断。
    """
    in_path_p = Path(in_path)
    src = in_path_p.read_text(encoding="utf-8")

    index = Index.create()
    # 跟你其它 mutator 一样的 include，保证能找到 csmith.h
    args = [
        "-I", "src/csmithedge/csmith/runtime",
        "-I", "src/csmithedge/csmith",
        "-std=c99",
    ]

    tu = index.parse(str(in_path_p), args=args)

    candidates = _collect_param_candidates(tu.cursor, str(in_path_p))
    print(f"[debug] total param candidates in seed file: {len(candidates)}")

    if not candidates:
        print("[debug] no suitable ParmVarDecl found; skip mutation")
        return False

    target_param = random.choice(candidates)
    func = target_param.semantic_parent
    assert func is not None

    old_type = target_param.type.spelling
    new_type = _build_new_param_type(old_type)
    name = target_param.spelling

    start_off = target_param.extent.start.offset
    end_off = target_param.extent.end.offset

    new_param_text = f"{new_type} {name}"

    print(
        "[debug] mutate param type: "
        f"func='{func.spelling}', param='{name}', "
        f"old_type='{old_type}' -> new_type='{new_type}'"
    )

    mutated = _apply_edits(src, [(start_off, end_off, new_param_text)])
    out_path_p = Path(out_path)
    out_path_p.parent.mkdir(parents=True, exist_ok=True)
    out_path_p.write_text(mutated, encoding="utf-8")

    print(f"[结果] 已生成变异文件: {out_path_p}")
    return True
