#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
P12-adv: 全局变量 static / extern 冲突（高级版）

目标：
  - 不再简单地“紧挨着原定义加一行 extern int g;”，而是模拟
    “头文件里把这个变量的类型/存储类搞错了”。

策略：
  1. 收集：
       - 种子文件里的所有全局变量 VarDecl（只要本 .c 文件的 TU 顶层变量）
       - 种子文件里定义的 struct 类型名（struct S0 / S1 ...）
  2. 随机选一个全局变量 g：
       - 原类型可能是 int / 指针 / struct / 其他
       - 看 storage class 是 static / extern / none
  3. 构造一个“故意冲突”的声明：
       - storage class 反过来：
           * 原来 static -> 新声明 extern
           * 原来 extern -> 新声明 static
           * 原来无 storage -> 随机选 static 或 extern
       - 类型做“强对比”变换：
           * 原来是标量整数 -> 用 struct / 指针 / double 等
           * 原来是指针 -> 用整型 / struct / 其他
           * 原来是 struct -> 用整型 / 指针 / 不同 struct
       - 偶尔加上 const / volatile 等限定符，让诊断更花
  4. 插入位置：
       - 在源文件中“最后一个 #include 行之后”插入这条假的声明，
         模拟“来自头文件的错误声明”，而真正的 static 定义还在后面。

典型诊断：
  - error: conflicting types for 'g_xxx'
  - note: previous declaration of 'g_xxx' was here

这样比“原地重复定义”要高级和贴近真实多了。
"""

import os
import random
from pathlib import Path
from typing import List

from clang import cindex


# --------------------- 通用 AST 工具 ---------------------


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


# --------------------- 收集 struct / 全局变量 ---------------------


def _collect_struct_types(tu: cindex.TranslationUnit, seed_path: str) -> List[str]:
    names = set()
    for c in _walk(tu.cursor):
        if (
            c.kind == cindex.CursorKind.STRUCT_DECL
            and c.spelling
            and _same_file(seed_path, c.location.file)
        ):
            names.add(c.spelling)
    if names:
        print(f"[debug] struct types in seed file: {[f'struct {n}' for n in sorted(names)]}")
    else:
        print("[debug] no struct types found in seed file")
    return sorted(names)


def _collect_global_vardecls(tu: cindex.TranslationUnit, seed_path: str):
    globals_ = []
    for c in _walk(tu.cursor):
        if (
            c.kind == cindex.CursorKind.VAR_DECL
            and _same_file(seed_path, c.location.file)
            and c.semantic_parent
            and c.semantic_parent.kind == cindex.CursorKind.TRANSLATION_UNIT
        ):
            name = c.spelling or ""
            # 避免动到 csmith runtime 的 __undefined 以及我们自己插入的 edgm_ 前缀
            if name.startswith("__") or name.startswith("edgm_"):
                continue
            globals_.append(c)
    print(f"[debug] global VarDecl in seed file: {len(globals_)}")
    return globals_


# --------------------- 类型分类 & 构造冲突类型 ---------------------


def _classify_type(typ: cindex.Type) -> str:
    """
    粗略分类：
      - scalar_int : 各种整型/枚举
      - pointer    : 指针
      - record     : struct/union
      - other      : 其他
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

    if canonical.kind == cindex.TypeKind.RECORD:
        return "record"

    return "other"


def _build_conflict_type(orig_kind: str, struct_names: List[str]) -> str:
    """
    根据原类型类别，构造一个“类型完全不搭”的新类型字符串。
    不做语义验证，只追求“够不一样 + 合法 C 语法”。
    """
    candidates: List[str] = []

    # 有 struct 类型的话，优先用它们搅局
    struct_ty = None
    if struct_names:
        struct_ty = f"struct {random.choice(struct_names)}"

    if orig_kind == "scalar_int":
        candidates.extend(["double", "uint64_t", "void *", "int64_t *"])
        if struct_ty:
            candidates.extend([struct_ty, f"{struct_ty} *"])

    elif orig_kind == "pointer":
        candidates.extend(["int", "uint32_t", "double", "void *"])
        if struct_ty:
            candidates.append(struct_ty)

    elif orig_kind == "record":
        candidates.extend(["int", "uint64_t", "void *", "int *"])
        if struct_ty:
            # 如果刚好只有一个 struct，就把它当“不同用途 struct”也可以
            candidates.append(f"{struct_ty} *")

    else:  # other
        candidates.extend(["int", "double", "void *", "uint8_t *"])
        if struct_ty:
            candidates.append(struct_ty)

    if not candidates:
        candidates = ["int"]

    base_ty = random.choice(candidates)

    # 随机加一点 const/volatile 增加“恶心度”
    qualifiers: List[str] = []
    if random.random() < 0.5:
        qualifiers.append("const")
    if random.random() < 0.3:
        qualifiers.append("volatile")

    ty_str = " ".join(qualifiers + [base_ty])
    print(f"[debug] choose conflicting type: '{ty_str}' (orig kind={orig_kind})")
    return ty_str


def _choose_storage_keyword(var_cursor: cindex.Cursor) -> str:
    """
    根据原来的 storage class 选择一个相反/冲突的 storage 关键字。
    """
    sc = var_cursor.storage_class
    from clang.cindex import StorageClass

    if sc == StorageClass.STATIC:
        return "extern"
    if sc == StorageClass.EXTERN:
        return "static"
    # 原来没有 storage，就随机搞一个
    return random.choice(["static", "extern"])


# --------------------- 插入位置选择 ---------------------


def _find_insert_line_after_includes(src: str) -> int:
    """
    找到“最后一个 #include 行之后”的插入点。
    返回 1-based 行号（和 clang 的 location.line 一致）。
    如果找不到 #include，就插在文件开头（第 1 行之前）。
    """
    lines = src.splitlines(keepends=True)
    last_inc_idx = -1
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#include"):
            last_inc_idx = i
    # 没有 include：插在文件第一行之前
    if last_inc_idx < 0:
        return 1
    # 否则插在最后一个 include 的下一行
    return last_inc_idx + 2  # 转换成 1-based 行号


# --------------------- 主变异逻辑 ---------------------


def mutate_var_static_extern_conflict_adv(in_path: str, out_path: str) -> bool:
    """
    对 in_path 的 C 源文件做 P12-adv 变异：
      - 选一个全局变量 g
      - 构造一个 storage class + 类型都不一样的声明
      - 在文件顶部 #include 之后插入该声明，制造 static/extern 类型冲突

    成功则写入 out_path，返回 True；否则返回 False。
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

    # 打印 AST 诊断（原本 seed 本身的一些 warning）
    for d in tu.diagnostics:
        loc = (
            f"{d.location.file}:{d.location.line}:{d.location.column}"
            if d.location.file
            else "<no-file>"
        )
        print(f"[AST diag] {loc}: {d.spelling}")

    struct_names = _collect_struct_types(tu, in_path)
    globals_ = _collect_global_vardecls(tu, in_path)

    if not globals_:
        print("[debug] no suitable global VarDecl; skip P12-adv mutation")
        return False

    # 随机选一个目标全局变量
    target = random.choice(globals_)
    g_name = target.spelling
    g_type = target.type
    kind = _classify_type(g_type)
    print(
        f"[debug] P12-adv: choose global '{g_name}', "
        f"orig_type='{g_type.spelling}', kind='{kind}'"
    )

    # 构造冲突声明
    sc_kw = _choose_storage_keyword(target)
    conflict_ty = _build_conflict_type(kind, struct_names)
    new_decl_line = f"{sc_kw} {conflict_ty} {g_name};\n"
    comment = f"/* P12-adv: conflicting declaration for {g_name} */\n"
    snippet = comment + new_decl_line + "\n"

    # 找插入位置：最后一个 #include 之后
    src_text = Path(in_path).read_text(encoding="utf-8")
    insert_line = _find_insert_line_after_includes(src_text)
    print(f"[debug] insert conflicting decl before original line {insert_line}")
    print("[debug] inserted snippet:\n" + snippet.rstrip())

    lines = src_text.splitlines(keepends=True)
    # 转 Python 下标
    idx = max(0, min(len(lines), insert_line - 1))
    lines.insert(idx, snippet)

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("".join(lines), encoding="utf-8")

    print(f"[结果] 已生成 P12-adv 变异文件: {out_path}")
    return True
